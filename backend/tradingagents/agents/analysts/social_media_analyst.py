from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage
import time
import json
import logging
from tradingagents.agents.utils.agent_utils import get_news
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.interface import get_prefetched_data

logger = logging.getLogger(__name__)


def create_social_media_analyst(llm):
    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        company_name = state["company_of_interest"]

        tools = [
            get_news,
        ]

        system_message = (
            "You are a social media and company specific news researcher/analyst tasked with analyzing social media posts, recent company news, and public sentiment for a specific company over the past week. Write a comprehensive long report detailing your analysis, insights, and implications for traders and investors on this company's current state after looking at social media and what people are saying about that company, analyzing sentiment data of what people feel each day about the company, and looking at recent company news. Try to look at all sources possible from social media to sentiment to news. Do not simply state the trends are mixed, provide detailed and finegrained analysis and insights that may help traders make decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read.""",
        )

        # Check if we have pre-fetched news data (reuse same company news)
        prefetched = get_prefetched_data(ticker, ["get_news"])

        if prefetched:
            # Fast path: data already available, skip tool calls
            logger.info(f"[SocialAnalyst] {ticker}: using pre-fetched data, skipping tool calls")
            data_msg = f"Here is the pre-fetched social media and company news data for {ticker}:\n\n"
            if "get_news" in prefetched:
                data_msg += f"## Company News & Social Sentiment\n{prefetched['get_news'][:5000]}\n\n"
            data_msg += "Please analyze this data focusing on social sentiment, public opinion, and implications for traders."

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a helpful AI assistant, collaborating with other assistants."
                        " Write a comprehensive social media and sentiment analysis report based on the provided data."
                        " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                        " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                        "\n{system_message}"
                        "For your reference, the current date is {current_date}. The current company we want to analyze is {ticker}",
                    ),
                    MessagesPlaceholder(variable_name="messages"),
                ]
            )
            prompt = prompt.partial(system_message=system_message, current_date=current_date, ticker=ticker)

            chain = prompt | llm
            messages = [HumanMessage(content=data_msg)]
            result = chain.invoke({"messages": messages})
            report = result.content

            return {
                "messages": [result],
                "sentiment_report": report,
            }
        else:
            # Original path: use tool calls
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a helpful AI assistant, collaborating with other assistants."
                        " Use the provided tools to progress towards answering the question."
                        " If you are unable to fully answer, that's OK; another assistant with different tools"
                        " will help where you left off. Execute what you can to make progress."
                        " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                        " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                        " You have access to the following tools: {tool_names}.\n{system_message}"
                        "For your reference, the current date is {current_date}. The current company we want to analyze is {ticker}",
                    ),
                    MessagesPlaceholder(variable_name="messages"),
                ]
            )

            prompt = prompt.partial(system_message=system_message)
            prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
            prompt = prompt.partial(current_date=current_date)
            prompt = prompt.partial(ticker=ticker)

            chain = prompt | llm.bind_tools(tools)
            result = chain.invoke(state["messages"])
            report = ""
            if len(result.tool_calls) == 0:
                report = result.content

            return {
                "messages": [result],
                "sentiment_report": report,
            }

    return social_media_analyst_node
