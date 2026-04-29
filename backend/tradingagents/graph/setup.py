# TradingAgents/graph/setup.py

import logging
import time as _time
from typing import Callable, Dict, Any
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode

from tradingagents.agents import *
from tradingagents.agents.utils.agent_states import AgentState

from .conditional_logic import ConditionalLogic

logger = logging.getLogger(__name__)


def _timed_node(name, fn, progress_callback: Callable[[dict], None] | None = None):
    """Wrap a graph node function with timing logs."""
    def wrapper(state):
        t0 = _time.time()
        ticker = state.get("company_of_interest", "?")
        if progress_callback:
            progress_callback({
                "symbol": ticker,
                "node": name,
                "status": "started",
                "timestamp": t0,
            })
        try:
            result = fn(state)
            return result
        finally:
            elapsed = _time.time() - t0
            logger.info(f"[TA-TIMING] {ticker} | {name} | {elapsed:.1f}s")
            if progress_callback:
                progress_callback({
                    "symbol": ticker,
                    "node": name,
                    "status": "completed",
                    "elapsed": elapsed,
                    "timestamp": _time.time(),
                })
    return wrapper


def _create_report_compressor(llm):
    """Create a node that compresses 4 analyst reports into concise summaries.

    This saves ~60-70% of input tokens for all downstream nodes (Bull/Bear
    researchers, Trader, Risk debaters, Risk Judge) which each re-read
    the full reports.
    """
    def report_compressor_node(state):
        market = state.get("market_report", "")
        sentiment = state.get("sentiment_report", "")
        news = state.get("news_report", "")
        fundamentals = state.get("fundamentals_report", "")

        # If reports are already short, skip compression
        total_len = len(market) + len(sentiment) + len(news) + len(fundamentals)
        if total_len < 4000:
            logger.info("[ReportCompressor] Reports already short, skipping")
            return {}

        prompt = f"""Compress these 4 analyst reports into concise summaries preserving ALL key data points, numbers, and actionable insights. Each summary should be 300-500 words max. Keep specific numbers, dates, percentages, and price targets.

## Market Analysis Report
{market[:4000]}

## Social Sentiment Report
{sentiment[:4000]}

## News Report
{news[:4000]}

## Fundamentals Report
{fundamentals[:4000]}

Output format - use these exact headers:
=== MARKET SUMMARY ===
[compressed market analysis]

=== SENTIMENT SUMMARY ===
[compressed sentiment analysis]

=== NEWS SUMMARY ===
[compressed news analysis]

=== FUNDAMENTALS SUMMARY ===
[compressed fundamentals analysis]"""

        try:
            response = llm.invoke(prompt)
            text = response.content

            # Parse compressed reports
            sections = {}
            for tag, key in [
                ("MARKET SUMMARY", "market_report"),
                ("SENTIMENT SUMMARY", "sentiment_report"),
                ("NEWS SUMMARY", "news_report"),
                ("FUNDAMENTALS SUMMARY", "fundamentals_report"),
            ]:
                marker = f"=== {tag} ==="
                start = text.find(marker)
                if start == -1:
                    continue
                start += len(marker)
                # Find next section or end
                next_marker = text.find("===", start + 1)
                section_text = text[start:next_marker].strip() if next_marker != -1 else text[start:].strip()
                if section_text:
                    sections[key] = section_text

            if len(sections) >= 3:
                compressed_total = sum(len(v) for v in sections.values())
                logger.info(
                    f"[ReportCompressor] Compressed {total_len} -> {compressed_total} chars "
                    f"({100 - compressed_total * 100 // total_len}% reduction)"
                )
                return sections
            else:
                logger.warning("[ReportCompressor] Failed to parse sections, keeping originals")
                return {}

        except Exception as e:
            logger.error(f"[ReportCompressor] Error: {e}, keeping original reports")
            return {}

    return report_compressor_node


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: ChatOpenAI,
        deep_thinking_llm: ChatOpenAI,
        tool_nodes: Dict[str, ToolNode],
        analyst_llm: ChatOpenAI = None,
        bull_memory=None,
        bear_memory=None,
        trader_memory=None,
        invest_judge_memory=None,
        risk_manager_memory=None,
        conditional_logic: ConditionalLogic = None,
        progress_callback: Callable[[dict], None] | None = None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.analyst_llm = analyst_llm or quick_thinking_llm
        self.tool_nodes = tool_nodes
        self.bull_memory = bull_memory
        self.bear_memory = bear_memory
        self.trader_memory = trader_memory
        self.invest_judge_memory = invest_judge_memory
        self.risk_manager_memory = risk_manager_memory
        self.conditional_logic = conditional_logic
        self.progress_callback = progress_callback

    def setup_graph(
        self, selected_analysts=["market", "social", "news", "fundamentals"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        if len(selected_analysts) == 0:
            raise ValueError("Trading Agents Graph Setup Error: no analysts selected!")

        # Create analyst nodes - use analyst_llm (may be Haiku for cost savings)
        analyst_nodes = {}
        delete_nodes = {}
        tool_nodes = {}

        if "market" in selected_analysts:
            analyst_nodes["market"] = create_market_analyst(
                self.analyst_llm
            )
            delete_nodes["market"] = create_msg_delete()
            tool_nodes["market"] = self.tool_nodes["market"]

        if "social" in selected_analysts:
            analyst_nodes["social"] = create_social_media_analyst(
                self.analyst_llm
            )
            delete_nodes["social"] = create_msg_delete()
            tool_nodes["social"] = self.tool_nodes["social"]

        if "news" in selected_analysts:
            analyst_nodes["news"] = create_news_analyst(
                self.analyst_llm
            )
            delete_nodes["news"] = create_msg_delete()
            tool_nodes["news"] = self.tool_nodes["news"]

        if "fundamentals" in selected_analysts:
            analyst_nodes["fundamentals"] = create_fundamentals_analyst(
                self.analyst_llm
            )
            delete_nodes["fundamentals"] = create_msg_delete()
            tool_nodes["fundamentals"] = self.tool_nodes["fundamentals"]

        # Wrap analyst nodes with timing
        for k in analyst_nodes:
            analyst_nodes[k] = _timed_node(
                f"{k.capitalize()} Analyst",
                analyst_nodes[k],
                self.progress_callback,
            )

        # Debate nodes: use analyst_llm (Haiku) for cost savings
        # Only Research Manager & Risk Judge use deep_thinking_llm (Sonnet) for final decisions
        bull_researcher_node = _timed_node("Bull Researcher",
            create_bull_researcher(self.analyst_llm), self.progress_callback)
        bear_researcher_node = _timed_node("Bear Researcher",
            create_bear_researcher(self.analyst_llm), self.progress_callback)
        research_manager_node = _timed_node("Research Manager",
            create_research_manager(self.deep_thinking_llm), self.progress_callback)
        trader_node = _timed_node("Trader",
            create_trader(self.analyst_llm), self.progress_callback)

        # Risk debate nodes: Haiku for debaters, Sonnet for judge
        aggressive_analyst = _timed_node("Aggressive Analyst",
            create_aggressive_debator(self.analyst_llm), self.progress_callback)
        neutral_analyst = _timed_node("Neutral Analyst",
            create_neutral_debator(self.analyst_llm), self.progress_callback)
        conservative_analyst = _timed_node("Conservative Analyst",
            create_conservative_debator(self.analyst_llm), self.progress_callback)
        risk_manager_node = _timed_node("Risk Judge",
            create_portfolio_manager(self.deep_thinking_llm), self.progress_callback)

        # Report compressor: uses analyst_llm (cheap) to compress reports
        # before they get duplicated across 8 downstream nodes
        report_compressor = _timed_node("Report Compressor",
            _create_report_compressor(self.analyst_llm), self.progress_callback)

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for analyst_type, node in analyst_nodes.items():
            workflow.add_node(f"{analyst_type.capitalize()} Analyst", node)
            workflow.add_node(
                f"Msg Clear {analyst_type.capitalize()}", delete_nodes[analyst_type]
            )
            workflow.add_node(f"tools_{analyst_type}", tool_nodes[analyst_type])

        # Add compressor node between analysts and debate phase
        workflow.add_node("Report Compressor", report_compressor)

        # Add other nodes
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)
        workflow.add_node("Trader", trader_node)
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Risk Judge", risk_manager_node)

        # Define edges
        # Start with the first analyst
        first_analyst = selected_analysts[0]
        workflow.add_edge(START, f"{first_analyst.capitalize()} Analyst")

        # Connect analysts in sequence
        for i, analyst_type in enumerate(selected_analysts):
            current_analyst = f"{analyst_type.capitalize()} Analyst"
            current_tools = f"tools_{analyst_type}"
            current_clear = f"Msg Clear {analyst_type.capitalize()}"

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{analyst_type}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Connect to next analyst, or to Report Compressor if last analyst
            if i < len(selected_analysts) - 1:
                next_analyst = f"{selected_analysts[i+1].capitalize()} Analyst"
                workflow.add_edge(current_clear, next_analyst)
            else:
                workflow.add_edge(current_clear, "Report Compressor")

        # Compressor -> Bull Researcher
        workflow.add_edge("Report Compressor", "Bull Researcher")

        # Add remaining edges
        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Risk Judge": "Risk Judge",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Risk Judge": "Risk Judge",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Risk Judge": "Risk Judge",
            },
        )

        workflow.add_edge("Risk Judge", END)

        # Compile and return
        return workflow.compile()
