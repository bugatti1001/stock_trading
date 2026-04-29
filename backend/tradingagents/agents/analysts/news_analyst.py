from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage
import time
import json
import logging
from tradingagents.agents.utils.agent_utils import get_news, get_global_news
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.interface import get_prefetched_data

logger = logging.getLogger(__name__)


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        tools = [
            get_news,
            get_global_news,
        ]

        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Do not simply state the trends are mixed, provide detailed and finegrained analysis and insights that may help traders make decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
        )

        # Check if we have pre-fetched data
        prefetched = get_prefetched_data(ticker, ["get_news", "get_global_news"])

        if prefetched:
            # Fast path: data already available, skip tool calls
            logger.info(f"[NewsAnalyst] {ticker}: using pre-fetched data, skipping tool calls")
            data_msg = f"Here is the pre-fetched news data for {ticker}:\n\n"
            if "get_news" in prefetched:
                data_msg += f"## Company-Specific News\n{prefetched['get_news'][:5000]}\n\n"
            if "get_global_news" in prefetched:
                data_msg += f"## Global Macroeconomic News\n{prefetched['get_global_news'][:5000]}\n\n"
            data_msg += "Please analyze this news data and write your comprehensive news analysis report."

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a helpful AI assistant, collaborating with other assistants."
                        " Write a comprehensive news analysis report based on the provided data."
                        " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                        " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                        "\n{system_message}"
                        "For your reference, the current date is {current_date}. We are looking at the company {ticker}",
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
                "news_report": report,
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
                        "For your reference, the current date is {current_date}. We are looking at the company {ticker}",
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
                "news_report": report,
            }

    return news_analyst_node
