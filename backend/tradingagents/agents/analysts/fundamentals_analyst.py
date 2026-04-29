from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage
import time
import json
import logging
from tradingagents.agents.utils.agent_utils import get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement, get_insider_transactions
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.interface import get_prefetched_data

logger = logging.getLogger(__name__)


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        company_name = state["company_of_interest"]

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Do not simply state the trends are mixed, provide detailed and finegrained analysis and insights that may help traders make decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
        )

        # Check if we have pre-fetched data
        prefetched = get_prefetched_data(ticker, ["get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"])

        if prefetched:
            # Fast path: data already available, skip tool calls
            logger.info(f"[FundamentalsAnalyst] {ticker}: using pre-fetched data, skipping tool calls")
            data_msg = f"Here is the pre-fetched fundamental data for {ticker}:\n\n"
            for key, label in [("get_fundamentals", "Company Fundamentals"), ("get_balance_sheet", "Balance Sheet"), ("get_cashflow", "Cash Flow Statement"), ("get_income_statement", "Income Statement")]:
                if key in prefetched:
                    data_msg += f"## {label}\n{prefetched[key][:3000]}\n\n"
            data_msg += "Please analyze this data and write your comprehensive fundamentals analysis report."

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a helpful AI assistant, collaborating with other assistants."
                        " Write a comprehensive fundamentals analysis report based on the provided data."
                        " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                        " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                        "\n{system_message}"
                        "For your reference, the current date is {current_date}. The company we want to look at is {ticker}",
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
                "fundamentals_report": report,
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
                        "For your reference, the current date is {current_date}. The company we want to look at is {ticker}",
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
                "fundamentals_report": report,
            }

    return fundamentals_analyst_node
