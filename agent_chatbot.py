import os
from typing import Generator, List
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langsmith import Client
from models import db, Message, ChatSession
from sql_tools import create_sql_query_tool, create_schema_info_tool

# Initialize LangSmith client if API key is available
if "LANGSMITH_API_KEY" in os.environ:
    try:
        langsmith_client = Client()
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "chatbot-sql-agent")
        print("✓ LangSmith tracing enabled")
    except Exception as e:
        print(f"⚠ LangSmith initialization failed: {e}")
else:
    print("ℹ LangSmith tracing disabled (no API key)")

# Ensure required environment variables
if "GOOGLE_API_KEY" not in os.environ:
    raise EnvironmentError("GOOGLE_API_KEY not found in environment variables.")

if "DATABASE_URL" not in os.environ:
    raise EnvironmentError("DATABASE_URL not found in environment variables.")

if "SAMPLE_DB_URL" not in os.environ:
    raise EnvironmentError("SAMPLE_DB_URL not found in environment variables.")


def create_agent():
    """
    Create the ReAct AI agent with SQL query capabilities.

    Returns:
        Configured ReAct agent
    """
    # Initialize the LLM
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash-exp",
        temperature=0.7,
        max_output_tokens=2048
    )

    # Create tools for SAMPLE database (not the chat database)
    sample_db_url = os.environ.get("SAMPLE_DB_URL")
    tools = [
        create_sql_query_tool(),
        create_schema_info_tool()
    ]

    # Create ReAct prompt template
    # ReAct agents use a specific format with thought/action/observation
    prompt = """
You are a helpful AI assistant with access to a PostgreSQL database.

Your capabilities:
1. Answer general questions naturally and conversationally
2. Query the database when users ask for statistics or data
3. Provide insights based on database information

When handling database queries:
- First understand what the user wants
- Use get_database_schema tool to understand table structure if needed
- Formulate a precise SELECT query
- Execute the query using query_database tool
- Present results in a clear, user-friendly format
- Explain what the data means

Safety rules (automatically enforced):
✓ Only SELECT queries are allowed
✓ No data modification operations
✓ Queries are rate-limited
✓ Results are automatically limited to 100 rows

Guidelines:
- Be conversational and friendly
- Ask clarifying questions if the user's request is ambiguous
- Explain your reasoning when executing queries
- Highlight key insights from the data
- If a query fails, explain why and suggest alternatives

You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

Begin!

Previous conversation history:
{chat_history}

Question: {input}
Thought: {agent_scratchpad}"""

    # Create the ReAct agent
    agent = create_agent(
        "gemini-2.5-flash",
        tools=tools,
        system_prompt=prompt
    )

    return agent, tools


def load_chat_history(session_id: int, limit: int = 10) -> str:
    """
    Load chat history for context as a formatted string.

    Args:
        session_id: Chat session ID
        limit: Maximum number of messages to load

    Returns:
        Formatted string of chat history
    """
    db_messages = db.session.scalars(
        db.select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()

    # Reverse to get chronological order
    db_messages = list(reversed(db_messages))

    # Format as string for ReAct prompt
    history_parts = []
    for msg in db_messages:
        if msg.role == "user":
            history_parts.append(f"Human: {msg.content}")
        elif msg.role == "ai":
            history_parts.append(f"Assistant: {msg.content}")

    return "\n".join(history_parts) if history_parts else "No previous conversation."


def execute_agent(agent, tools, input_text: str, chat_history: str, max_iterations: int = 5) -> str:
    """
    Manually execute the ReAct agent with iteration control.

    Args:
        agent: The ReAct agent runnable
        tools: List of tools available to the agent
        input_text: User input
        chat_history: Formatted chat history
        max_iterations: Maximum number of agent iterations

    Returns:
        Final agent response
    """
    # Create a tool map for quick lookup
    tool_map = {tool.name: tool for tool in tools}

    # Initialize agent state
    agent_scratchpad = ""
    intermediate_steps = []

    for iteration in range(max_iterations):
        try:
            # Invoke agent with current state
            result = agent.invoke({
                "input": input_text,
                "chat_history": chat_history,
                "agent_scratchpad": agent_scratchpad,
                "tools": "\n".join([f"{tool.name}: {tool.description}" for tool in tools]),
                "tool_names": ", ".join([tool.name for tool in tools])
            })

            # Extract the response
            response = result.content if hasattr(result, 'content') else str(result)

            # Check if we have a final answer
            if "Final Answer:" in response:
                # Extract and return final answer
                final_answer = response.split("Final Answer:")[-1].strip()
                return final_answer

            # Parse action and action input
            if "Action:" in response and "Action Input:" in response:
                action_part = response.split("Action:")[1].split("Action Input:")[0].strip()
                action_input_part = response.split("Action Input:")[1].split("\n")[0].strip()

                # Execute the tool
                if action_part in tool_map:
                    tool = tool_map[action_part]
                    observation = tool.func(action_input_part)

                    # Update scratchpad with observation
                    agent_scratchpad += f"\n{response}\nObservation: {observation}\nThought: "
                    intermediate_steps.append((action_part, action_input_part, observation))
                else:
                    # Invalid action
                    agent_scratchpad += f"\n{response}\nObservation: Error - Tool '{action_part}' not found. Available tools: {', '.join(tool_map.keys())}\nThought: "
            else:
                # No clear action, treat as final answer
                return response.split("Thought:")[-1].strip()

        except Exception as e:
            print(f"Agent iteration error: {e}")
            return f"I encountered an error while processing your request: {str(e)}"

    # Max iterations reached
    return "I've reached my thinking limit. Could you rephrase your question or break it into smaller parts?"


def stream_agent_response(session_id: int, user_message: str) -> Generator[str, None, None]:
    """
    Stream the AI agent's response with database query capabilities.

    Args:
        session_id: Chat session ID
        user_message: User's message

    Yields:
        String chunks of the response
    """
    try:
        # Create agent
        agent, tools = create_agent()

        # Load chat history
        chat_history = load_chat_history(session_id, limit=10)

        # Filter out the current user message if it's already in history
        if user_message in chat_history:
            history_lines = chat_history.split("\n")
            history_lines = [line for line in history_lines if user_message not in line or "Assistant:" in line]
            chat_history = "\n".join(history_lines)

        # Execute the agent
        output = execute_agent(agent, tools, user_message, chat_history, max_iterations=5)

        # Stream the output in chunks (simulate streaming)
        chunk_size = 10  # words per chunk
        words = output.split()

        for i in range(0, len(words), chunk_size):
            chunk = " ".join(words[i:i + chunk_size])
            if i + chunk_size < len(words):
                chunk += " "
            yield chunk

    except Exception as e:
        error_msg = f"I apologize, but I encountered an error: {str(e)}"
        print(f"Agent error: {e}")
        yield error_msg


def stream_chat_response(session_id: int, user_message: str) -> Generator[str, None, None]:
    """
    Main entry point for streaming chat responses.
    Integrates with the ReAct agent for database queries.

    This replaces the original chatbot.py implementation.

    Args:
        session_id: Chat session ID
        user_message: User's message

    Yields:
        String chunks of the AI response
    """
    yield from stream_agent_response(session_id, user_message)