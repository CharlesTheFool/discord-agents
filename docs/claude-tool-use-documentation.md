# Claude Tool Use - Complete Documentation

## Table of Contents

1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [Tool Types](#tool-types)
4. [How Tool Use Works](#how-tool-use-works)
5. [Implementation Guide](#implementation-guide)
6. [Tool Patterns](#tool-patterns)
7. [Advanced Features](#advanced-features)
8. [Best Practices](#best-practices)
9. [Error Handling](#error-handling)
10. [Pricing](#pricing)
11. [Troubleshooting](#troubleshooting)

---

## Overview

Tool use (also known as function calling) enables Claude to interact with external systems, APIs, and data sources to extend its capabilities beyond its training data. Claude doesn't directly execute tools—instead, it participates in a conversation where it signals intent to use predefined tools, receives results, and formulates responses based on those results.

### Key Capabilities

- **Real-time information access**: Fetch current data beyond Claude's knowledge cutoff
- **External system integration**: Connect to APIs, databases, and custom services
- **Structured interactions**: Receive properly formatted requests with validated parameters
- **Dynamic responses**: Adapt answers based on live, external data

---

## Core Concepts

### The Tool Use Paradigm

Claude operates on a request-response cycle when using tools:

1. **Tool Definition**: You define available tools with names, descriptions, and input schemas
2. **Claude's Decision**: Claude analyzes whether a tool would help answer the query
3. **Tool Request**: If needed, Claude generates a structured tool use request
4. **Execution**: Your application executes the tool and returns results
5. **Response Formation**: Claude incorporates results into its final answer

### Critical Principle

**Claude never executes tools directly.** Your application must:
- Extract tool requests from Claude's responses
- Execute the actual tool/function code
- Return results back to Claude in the proper format

---

## Tool Types

Claude supports two fundamental categories of tools:

### 1. Client Tools

Tools that execute on your systems and require implementation by you.

**Types:**
- **User-defined custom tools**: Any function or API call you create
- **Anthropic-defined tools**: Pre-specified tools requiring client implementation
  - `computer_use` - Control desktop environments
  - `text_editor_20250124` - File editing operations
  - `bash_20250124` - Execute bash commands

**Characteristics:**
- Stop reason: `tool_use` when Claude requests the tool
- You handle all execution logic
- You return results in `tool_result` content blocks

### 2. Server Tools

Tools that execute on Anthropic's servers automatically.

**Available Server Tools:**
- **Web Search** (`web_search_20250305`): Search the internet for current information
- **Web Fetch**: Retrieve and analyze webpage content from URLs

**Characteristics:**
- No client implementation needed
- Specified in API request but executed by Claude
- Results automatically incorporated into responses
- Additional per-use costs apply ($10 per 1,000 web searches)

**Important:** Use versioned tool names (e.g., `web_search_20250305`) to ensure compatibility across model versions.

---

## How Tool Use Works

### Client Tool Workflow

```
┌─────────────────────────────────────────────────────────┐
│ Step 1: Provide Claude with Tools and User Prompt      │
│                                                          │
│ - Define tools (name, description, input schema)        │
│ - Include user query requiring tool assistance          │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ Step 2: Claude Decides to Use Tool                      │
│                                                          │
│ - Assesses if tools can help with query                 │
│ - Constructs formatted tool use request                 │
│ - Returns stop_reason: "tool_use"                       │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ Step 3: Execute Tool and Return Results                 │
│                                                          │
│ - Extract tool name and input from Claude's request     │
│ - Execute tool code on your system                      │
│ - Return results in tool_result content block           │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ Step 4: Claude Formulates Final Response                │
│                                                          │
│ - Analyzes tool results                                 │
│ - Crafts natural language response to original query    │
└─────────────────────────────────────────────────────────┘
```

### Server Tool Workflow

```
┌─────────────────────────────────────────────────────────┐
│ Step 1: Provide Claude with Server Tools and Prompt    │
│                                                          │
│ - Specify server tools (web_search, web_fetch)         │
│ - Include user query needing real-time data            │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ Step 2: Claude Executes Server Tool                     │
│                                                          │
│ - Determines tool can help with query                   │
│ - Executes tool automatically                           │
│ - Results incorporated into response                    │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│ Step 3: Claude Uses Results to Formulate Response       │
│                                                          │
│ - Analyzes server tool results                          │
│ - Provides answer with citations (for web search)       │
│ - No additional user interaction needed                 │
└─────────────────────────────────────────────────────────┘
```

---

## Implementation Guide

### Basic Tool Definition Structure

```json
{
  "name": "tool_name",
  "description": "Clear description of what this tool does",
  "input_schema": {
    "type": "object",
    "properties": {
      "parameter1": {
        "type": "string",
        "description": "Description of this parameter"
      },
      "parameter2": {
        "type": "number",
        "description": "Description of this parameter"
      }
    },
    "required": ["parameter1"]
  }
}
```

### Single Tool Example

**Tool Definition:**
```python
tools = [{
    "name": "get_weather",
    "description": "Get the current weather in a given location",
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City and state, e.g., San Francisco, CA"
            },
            "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "Temperature unit (default: fahrenheit)"
            }
        },
        "required": ["location"]
    }
}]
```

**Making the Request:**
```python
response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    tools=tools,
    messages=[{"role": "user", "content": "What's the weather in San Francisco?"}]
)
```

**Handling Claude's Response:**
```python
if response.stop_reason == "tool_use":
    tool_use_block = next(block for block in response.content if block.type == "tool_use")
    tool_name = tool_use_block.name
    tool_input = tool_use_block.input
    
    # Execute the tool
    weather_data = get_weather(tool_input["location"], tool_input.get("unit", "fahrenheit"))
    
    # Return result to Claude
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        tools=tools,
        messages=[
            {"role": "user", "content": "What's the weather in San Francisco?"},
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use_block.id,
                    "content": weather_data
                }]
            }
        ]
    )
```

### Controlling Tool Choice

The `tool_choice` parameter controls how Claude decides to use tools:

```python
# Option 1: auto (default) - Claude decides
tool_choice = {"type": "auto"}

# Option 2: any - Claude must use at least one tool
tool_choice = {"type": "any"}

# Option 3: tool - Force specific tool usage
tool_choice = {"type": "tool", "name": "get_weather"}

# Option 4: none - Disable all tool use
tool_choice = {"type": "none"}
```

**Important Notes:**
- When using `any` or `tool`, Claude will not provide natural language explanations before the tool use
- These options prefill the assistant message to force tool usage
- Extended thinking mode is incompatible with `any` and `tool` options

---

## Tool Patterns

### Pattern 1: Parallel Tool Use

Claude can request multiple tools simultaneously when operations are independent.

**Example Conversation:**
```
User: "What's the weather in SF and NYC?"

Claude: [tool_use: get_weather(location="San Francisco, CA")]
        [tool_use: get_weather(location="New York, NY")]

User: [tool_result for SF: "72°F, sunny"]
      [tool_result for NYC: "65°F, cloudy"]

Claude: "In San Francisco it's 72°F and sunny, while New York 
        is cooler at 65°F with cloudy skies."
```

**Critical Rule:** All tool results from parallel calls must be returned in a **single user message**. Never split them across multiple messages.

**Correct Format:**
```python
{
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_01A",
            "content": "Result 1"
        },
        {
            "type": "tool_result",
            "tool_use_id": "toolu_01B",
            "content": "Result 2"
        }
    ]
}
```

### Pattern 2: Sequential Tools (Tool Chaining)

Claude calls one tool, uses its result to inform the next tool call.

**Example:**
```
User: "What's the weather where I am?"

Claude: [tool_use: get_location()]

User: [tool_result: "San Francisco, CA"]

Claude: [tool_use: get_weather(location="San Francisco, CA", unit="fahrenheit")]

User: [tool_result: "59°F (15°C), mostly cloudy"]

Claude: "Based on your current location in San Francisco, CA, the weather 
        is 59°F (15°C) and mostly cloudy. You may want a light jacket."
```

### Pattern 3: Missing Information Handling

When required parameters are missing:

**Claude Opus Behavior:** More likely to ask for missing information
**Claude Sonnet Behavior:** May attempt to infer reasonable values

**Example with Missing Location:**
```
User: "What's the weather?"

Claude Sonnet might infer: [tool_use: get_weather(location="San Francisco, CA")]

Claude Opus more likely: "I need to know which location you'd like 
                          the weather for. Could you provide a city?"
```

**Best Practice:** Use chain-of-thought prompting to encourage parameter validation:

```
System: "Before calling a tool, analyze each required parameter. 
If any required parameter cannot be determined from the user's 
request, ask for clarification. DO NOT invoke functions with 
missing required parameters."
```

### Pattern 4: JSON Mode (Structured Output)

Force Claude to return structured JSON by defining a tool for the output format.

**Setup:**
```python
tools = [{
    "name": "record_summary",
    "description": "Record a structured summary of the image",
    "input_schema": {
        "type": "object",
        "properties": {
            "key_colors": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
            "estimated_year": {"type": "integer"}
        },
        "required": ["key_colors", "description"]
    }
}]

# Force this tool to be used
tool_choice = {"type": "tool", "name": "record_summary"}
```

**Key Points:**
- Tool name and description should be from Claude's perspective
- Set `tool_choice` to force the specific tool
- The `input` to the tool becomes your structured output

---

## Advanced Features

### Chain-of-Thought Tool Use

Prompt Claude to think before using tools:

```
System Prompt:
"Before calling a tool, do some analysis:
1. Think about which tool is relevant to the user's request
2. Go through each required parameter and determine if the user 
   has provided or given enough information to infer a value
3. If all required parameters are present or can be reasonably 
   inferred, proceed with the tool call
4. If a required parameter is missing, DO NOT invoke the function
   (not even with fillers) and instead ask the user for it
5. DO NOT ask for more information on optional parameters"
```

### Token-Efficient Tool Use (Beta)

Claude Sonnet 3.7 supports a token-efficient mode:

```python
headers = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "token-efficient-tools-2025-02-19"
}
```

**Benefits:**
- Saves 14-70% on output tokens
- Reduces latency
- Same functionality, lower cost

### Web Search with Citations

When using web search, Claude provides citations:

**Response Structure:**
```json
{
  "content": [
    {
      "type": "text",
      "text": "Claude Shannon was born on April 30, 1916",
      "citations": [
        {
          "type": "web_search_result_location",
          "url": "https://en.wikipedia.org/wiki/Claude_Shannon",
          "title": "Claude Shannon - Wikipedia",
          "cited_text": "Claude Elwood Shannon (April 30, 1916 – February 24, 2001)..."
        }
      ]
    }
  ]
}
```

**Important:** Citation fields don't count toward token usage.

### Prompt Caching with Tools

Enable prompt caching by adding `cache_control` breakpoints:

```python
{
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "...",
            "content": "...",
            "cache_control": {"type": "ephemeral"}
        }
    ]
}
```

The system automatically caches up to the last `web_search_tool_result` block.

### Handling Long-Running Turns

Server tools may return `pause_turn` as the stop reason:

```python
if response.stop_reason == "pause_turn":
    # Provide the response back as-is to let Claude continue
    next_response = client.messages.create(
        model="claude-sonnet-4-20250514",
        messages=messages + [{"role": "assistant", "content": response.content}]
    )
```

---

## Best Practices

### Tool Definition Best Practices

1. **Clear, Descriptive Names**
   - Use verb-noun format: `get_weather`, `search_database`, `send_email`
   - Make purpose immediately obvious

2. **Comprehensive Descriptions**
   - Explain what the tool does
   - Mention when it should/shouldn't be used
   - Include any important constraints or limitations

3. **Detailed Parameter Descriptions**
   - Describe each parameter's purpose
   - Provide examples of valid inputs
   - Clarify units, formats, or constraints
   - Mark required vs. optional clearly

4. **Use Enums When Appropriate**
   ```json
   {
     "type": "string",
     "enum": ["celsius", "fahrenheit", "kelvin"],
     "description": "Temperature unit"
   }
   ```

### Model Selection Guidelines

**Claude Opus 4.1 / Opus 4 / Sonnet 4.5 / Sonnet 4 / Sonnet 3.7:**
- Complex tool scenarios
- Multiple tool interactions
- Ambiguous queries requiring clarification
- Critical applications needing parameter validation

**Claude Haiku 3.5 / Haiku 3:**
- Straightforward tool use
- Single tool scenarios
- Cost-sensitive applications
- Note: May infer missing parameters rather than asking

### Prompt Engineering for Tools

**Provide Context:**
```
"You are a helpful assistant with access to weather data.
Use the get_weather tool to provide current conditions when asked."
```

**Set Expectations:**
```
"If you need to use multiple tools, explain your reasoning.
Always cite your sources when using web search."
```

**Handle Errors Gracefully:**
```
"If a tool returns an error, acknowledge it and either:
1. Try an alternative approach
2. Explain the limitation to the user"
```

### Parallel Tool Use Optimization

To maximize parallel tool calls:

**Do:**
- Define independent tools clearly
- Use explicit prompts: "Check weather in both SF and NYC"
- Return all results in one message
- Maintain consistent message structure

**Don't:**
- Split tool results across multiple user messages
- Create tools with interdependencies
- Mix different tool result formats

### Error Handling in Tool Execution

**Robust Error Handling:**
```python
def execute_tool(tool_name, tool_input):
    try:
        if tool_name == "get_weather":
            result = get_weather(**tool_input)
            return {"content": result, "is_error": False}
    except LocationNotFound as e:
        return {
            "content": f"Error: Location '{tool_input['location']}' not found",
            "is_error": True
        }
    except APIError as e:
        return {
            "content": f"Error: Weather service temporarily unavailable",
            "is_error": True
        }
```

**Return Error Details to Claude:**
```python
{
    "type": "tool_result",
    "tool_use_id": "...",
    "content": "Error: Location 'Atlantis' not found in weather database",
    "is_error": True
}
```

---

## Error Handling

### Common Error Scenarios

**1. Tool Not Used When Expected**

**Cause:** Insufficient tool description or unclear parameters

**Solution:**
- Enhance tool descriptions
- Add examples to parameter descriptions
- Use more explicit user prompts

**2. Wrong Tool Selected**

**Cause:** Overlapping tool descriptions or ambiguous queries

**Solution:**
- Clarify tool descriptions with distinct use cases
- Use chain-of-thought prompting
- Provide more context in the user query

**3. Missing Tool Parameters**

**Cause:** Required information not in user query

**Solution:**
- Implement chain-of-thought validation
- Use Claude Opus models for better clarification
- Design tools with sensible optional defaults

**4. Parallel Tools Not Used**

**Cause:** Incorrect tool result formatting in conversation history

**Solution:**
- Ensure all parallel results in single message
- Check message structure consistency
- Review conversation history format

**5. `max_tokens` Reached During Tool Use**

**Handling:**
```python
if response.stop_reason == "max_tokens":
    # Tool use may be incomplete
    # Increase max_tokens or handle gracefully
    if response.content[-1].type == "tool_use":
        # Tool call may be truncated - handle carefully
        pass
```

### Server Tool Error Codes

**Web Search Errors:**
- `max_uses_exceeded`: Search limit reached
- `network_error`: Connection issues
- `invalid_request`: Malformed search query

**Important:** Claude handles server tool errors transparently—you don't need to manage `is_error` results for them.

### Debugging Tool Use

**Log Everything:**
```python
import logging

logging.info(f"User query: {user_message}")
logging.info(f"Tools provided: {[tool['name'] for tool in tools]}")
logging.info(f"Claude response: {response}")
logging.info(f"Stop reason: {response.stop_reason}")

if response.stop_reason == "tool_use":
    for block in response.content:
        if block.type == "tool_use":
            logging.info(f"Tool requested: {block.name}")
            logging.info(f"Tool input: {block.input}")
```

**Analyze Failures:**
1. Did Claude understand the tool description?
2. Were the parameters clear?
3. Was the result format what Claude expected?
4. Did error messages provide enough context?

---

## Pricing

Tool use requests are priced based on:

1. **Input tokens** (including `tools` parameter definitions)
2. **Output tokens** generated
3. **Server tool usage** (e.g., $10 per 1,000 web searches)
4. **Tool use system prompt** (added automatically)

### Token Costs Breakdown

**What Counts as Tokens:**
- Tool names, descriptions, and schemas in `tools` parameter
- `tool_use` content blocks in requests/responses
- `tool_result` content blocks in requests
- Automatic tool use system prompt

### System Prompt Token Counts

| Model | tool_choice: auto/none | tool_choice: any/tool |
|-------|------------------------|----------------------|
| Claude Opus 4.1 | 346 tokens | 313 tokens |
| Claude Opus 4 | 346 tokens | 313 tokens |
| Claude Sonnet 4.5 | 346 tokens | 313 tokens |
| Claude Sonnet 4 | 346 tokens | 313 tokens |
| Claude Sonnet 3.7 | 346 tokens | 313 tokens |
| Claude Sonnet 3.5 (Oct) | 345 tokens | 313 tokens |
| Claude Sonnet 3.5 | 261 tokens | - |
| Claude Opus 3 | 340 tokens | - |
| Claude Haiku 3.5 | 281 tokens | - |
| Claude Haiku 3 | 235 tokens | - |

**Note:** Zero tokens if no tools provided and `tool_choice` is `none`.

### Web Search Specific Costs

- **Per search:** $10 per 1,000 searches
- **Plus:** Standard token costs for generated output
- **No charge:** If search encounters an error
- **Search results:** Count toward input tokens

### Cost Optimization Strategies

1. **Use Token-Efficient Mode** (Claude Sonnet 3.7)
   - 14-70% token savings on average
   
2. **Minimize Tool Definitions**
   - Only include relevant tools for each request
   - Keep descriptions concise but clear
   
3. **Use Prompt Caching**
   - Cache tool definitions for repeated use
   - Cache search results in multi-turn conversations
   
4. **Choose Appropriate Models**
   - Haiku for simple, straightforward tool use
   - Sonnet/Opus for complex scenarios

---

## Troubleshooting

### Claude Doesn't Use Tools

**Checklist:**
- [ ] Are tool descriptions clear and specific?
- [ ] Do parameter descriptions include examples?
- [ ] Is the user query actually relevant to the tools?
- [ ] Is `tool_choice` set to `none`? (If so, tools are disabled)

**Fix:** Enhance descriptions and be more explicit in user prompts.

### Wrong Tool Gets Called

**Checklist:**
- [ ] Do tool descriptions overlap in purpose?
- [ ] Is each tool's use case distinct?
- [ ] Does the user query contain enough context?

**Fix:** Clarify tool purposes and add constraints in descriptions.

### Parameters Are Incorrect

**Checklist:**
- [ ] Are parameter types specified correctly?
- [ ] Are examples provided in parameter descriptions?
- [ ] Are constraints (enums, ranges) defined?
- [ ] Is the required/optional distinction clear?

**Fix:** Add more detailed parameter documentation and examples.

### Tool Results Don't Help

**Checklist:**
- [ ] Is the tool returning relevant information?
- [ ] Is the result format what Claude expects?
- [ ] Are error messages informative?
- [ ] Is there enough context in the result?

**Fix:** Improve tool implementation and result formatting.

### Parallel Tools Not Working

**Checklist:**
- [ ] Are all tool results in a single user message?
- [ ] Is the message format consistent?
- [ ] Are tools truly independent?
- [ ] Is conversation history formatted correctly?

**Fix:** Consolidate tool results into one message; review formatting.

### Performance Issues

**Checklist:**
- [ ] Are you using the most efficient model for your use case?
- [ ] Could you use token-efficient mode?
- [ ] Is prompt caching enabled?
- [ ] Are tool definitions overly complex?

**Fix:** Optimize model selection, enable caching, simplify tools.

---

## Additional Resources

### Code Examples

Explore Anthropic's cookbook for ready-to-implement examples:

1. **Calculator Tool**: Simple numerical computation integration
2. **Customer Service Agent**: Responsive bot leveraging client tools
3. **Extracting Structured JSON**: Data extraction from unstructured text

### Server Tools Documentation

- **Web Search Tool**: `/en/docs/agents-and-tools/tool-use/web-search-tool`
- **Web Fetch Tool**: `/en/docs/agents-and-tools/tool-use/web-fetch-tool`
- **Computer Use Tool**: `/en/docs/agents-and-tools/tool-use/computer-use-tool`
- **Text Editor Tool**: `/en/docs/agents-and-tools/tool-use/text-editor-tool`

### Extended Thinking with Tool Use

When using extended thinking mode with tool use:
- Only `tool_choice: auto` and `tool_choice: none` are supported
- Use phrases like "think", "think hard", "think harder", "ultrathink" for increased thinking budget
- Ideal for complex architectural decisions and multi-step implementations

---

## Summary

Tool use transforms Claude from a conversational AI into an active agent capable of:

- **Accessing real-time data** through web search and APIs
- **Executing code** and system commands
- **Interacting with external systems** via custom integrations
- **Providing structured outputs** through JSON mode
- **Chaining complex operations** through sequential tool calls

**Key Principles:**
1. Claude never executes tools—your code does
2. Clear descriptions and schemas are crucial
3. Error handling should be robust and informative
4. Choose the right model for your complexity level
5. Monitor costs through efficient tool design

**Success Formula:**
```
Great Tool Definitions + Clear Prompts + Robust Execution + Proper Error Handling = Powerful AI Applications
```

Start simple with a single tool, validate it works, then expand to more complex scenarios. Tool use is an iterative process—refine based on real-world usage and Claude's responses.
