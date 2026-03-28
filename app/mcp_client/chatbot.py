import json
import logging

from app.config import Configuration
from app.llm_config import GEMINIConfig, OPENAIConfig, LLMConfiguration
from app.llm_provider import GeminiProvider,OpenAIProvider
from app.mcp_client.server import Server
from app.mcp_client.prompts import DEFINE_TOOLS_PROMPT, STRUCTURE_OUTPUT_PROMPT

class LLMClient:
    def __init__(self, llm_providers: dict):
        self.providers = llm_providers  # Creates an instance of the providers


class ChatSession:
    """Orchestrates the interaction between user, LLM, and tools."""

    def __init__(self, servers: list[Server], llm_client: LLMClient, user_ip: str = None) -> None:
        self.servers: list[Server] = servers
        self.llm_client: LLMClient = llm_client
        self.memory = []
        self.system_message = None
        self.messages: list = None
        self.user_ip = user_ip
        self.tools = None 
        self.logger=logging.getLogger(__class__.__name__)

    @property
    def context(self):
        seen = set()
        result = []

        for msg in self.messages[:3] + self.messages[-12:]:
            # Convert dicts to a hashable representation
            key = tuple(msg.items()) if isinstance(msg, dict) else msg
            if key not in seen:
                seen.add(key)
                result.append(msg)

        return result

    @classmethod
    async def create(cls, servers: list[Server], llm_client: LLMClient, user_ip: str = None) -> "ChatSession":
        self = cls(servers, llm_client, user_ip)
        
        try:
            for server in self.servers:
                try:
                    await server.initialize()
                except Exception as e:
                    self.logger.error(f"Failed to initialize server {server.name}: {e}")
                    await self.cleanup_servers()
                    raise RuntimeError(f"Server initialization failed: {e}") from e

            # Gather tools
            all_tools = []
            try:
                for server in self.servers:
                    tools = await server.list_tools()
                    self.logger.info(f"Type of tools: {type(tools)} for {server.name}")
                    self.tools=tools
                    all_tools.extend(tools)
                tools_description = [tool.__dict__ for tool in all_tools]
            except Exception as e:
                self.logger.error(f"Error listing tools: {e}")
                raise RuntimeError(f"Tool listing failed: {e}") from e

            # Compose system message
            self.system_message = DEFINE_TOOLS_PROMPT.format(tools_description = tools_description)

            self.messages = [{"role": "user", "content": self.system_message}]

            return self

        except Exception:
            await self.cleanup_servers()
            raise
    
    async def cleanup_servers(self) -> None:
        """Clean up all server resources safely."""
        if not self.servers:
            return

        for server in self.servers:
            try:
                await server.cleanup()
                self.logger.info("server cleared up successfully.")
            except Exception as e:
                # Silently handle cleanup errors to ensure all servers get cleanup attempts
                self.logger.error(f"Error cleaning up server {server.name}: {e}")
                pass

    async def process_llm_response(self, llm_response: str) -> str:
        """Process the LLM response and execute tools if needed.
        Args:
            llm_response: The response from the LLM.
        Returns:
            The result of tool execution or the original response.
        """
        # If llm_response is already a dict (not a string), use it directly
        if not isinstance(llm_response, str):
            tool_call = llm_response
        else:
            # Try to parse the response as JSON
            try:
                tool_call = json.loads(llm_response)
            except json.JSONDecodeError:
                # If direct parsing fails, try to extract JSON from the response
                json_start = llm_response.find('{')
                json_end = llm_response.rfind('}') + 1
                if json_start != -1 and json_end != -1:
                    json_str = llm_response[json_start:json_end]
                    try:
                        tool_call = json.loads(json_str)
                    except json.JSONDecodeError:
                        # Try cleaning the JSON string
                        try:
                            cleaned_json = json_str.replace('\n', '').strip()
                            cleaned_json = cleaned_json.replace("'", '"')  # Replace single quotes with double quotes
                            cleaned_json = cleaned_json.replace(',}', '}').replace(',]', ']')  # Remove trailing commas
                            tool_call = json.loads(cleaned_json)
                        except json.JSONDecodeError:
                            # Try using ast.literal_eval as a last resort
                            try:
                                import ast
                                tool_call = ast.literal_eval(json_str)
                            except (SyntaxError, ValueError) as e:
                                self.logger.warning(f"Failed to parse JSON from response: {e}")
                                self.logger.warning(f"Original response: {llm_response}")
                                return llm_response
                else:
                    # If no JSON-like structure is found, return the original response
                    return llm_response
        
        self.logger.info(f" the tool called: {tool_call}")
        if "tool" in tool_call and "arguments" in tool_call:
            # Ensure arguments is a dictionary
            if not isinstance(tool_call["arguments"], dict):
                self.logger.warning(f"Arguments is not a dictionary: {tool_call['arguments']}")
                return llm_response
                
            for server in self.servers:
                tools = self.tools  # Use stored tools
                
                if any(tool.name == tool_call["tool"] for tool in tools):
                    try:
                        result = await server.execute_tool(
                            tool_call["tool"], tool_call["arguments"]
                        )
                        if isinstance(result.content, list) and len(result.content) > 0:
                            content_type = result.content[0].type
                            content_text = result.content[0].text
                            annotations = result.content[0].annotations
                            self.logger.info(f" The return type: {content_type} Annotations: {annotations} Content : {content_text} type: {type(content_text)}")
                        
                            if isinstance(content_text, str):
                                try:
                                    content_text = json.loads(content_text)
                                    self.logger.info(f"Parsed JSON content, type: {type(content_text)}")
                                except json.JSONDecodeError:
                                    self.logger.warning("Content is not valid JSON.")
                            if isinstance(content_text, dict) and content_text.get("action_link"):
                                return content_text
                            else:
                                struct_prompt = STRUCTURE_OUTPUT_PROMPT.format(content_text = content_text)
                                return  struct_prompt
                        
                        else:
                            return "Tool execution result: No content returned."
                    except Exception as e:
                        error_msg = f"Error executing tool: {str(e)}"
                        return error_msg
            return f"No server found with tool: {tool_call['tool']}"
        return llm_response

    async def respond(self, model_id: str, user_input: str) -> str:
        """Handle a user input and return the assistant's response."""
        # Process user input

        try:
            self.messages.append({"role": "user", "content": user_input})
            self.memory.append({"role": "user", "content": user_input})

            # Validate model_id
            if model_id not in self.llm_client.providers:
                raise ValueError(f"Invalid model_id: {model_id}. Available providers: {list(self.llm_client.providers.keys())}")

            # Get LLM response
            llm_response = await self.llm_client.providers[model_id].get_response(self.context)

            # Process potential tool calls
            result = await self.process_llm_response(llm_response)

            if result != llm_response:
                # Tool was used; get a final response
                self.messages.append({"role": "assistant", "content": f"{llm_response}"})
                if isinstance(result, dict):
                    self.messages.append({"role": "user", "content":"\n".join(f"{key}: {value}" for key, value in result.items())})
                else:
                    self.messages.append({"role": "user", "content": result})

                if isinstance(result, dict) and result.get("action_link"):
                    final_response = result
                    try:
                        
                        self.messages.append({"role": "assistant", "content": f"Generated form for {final_response['entity']} name: {final_response['name']}, return form link: {final_response['action_link']}"})
                        self.memory.append({"role": "assistant", "content": f"Generated form for {final_response['entity']} name: {final_response['name']}, return form link: {final_response['action_link']}"})
                    except Exception as e:
                        raise                
                else:
                    final_response = await self.llm_client.providers[model_id].get_response(self.context)
                    self.messages.append({"role": "assistant", "content": final_response})
                    self.memory.append({"role": "assistant", "content": final_response})
                # print(self.messages)
                return final_response
            else:
                self.messages.append({"role": "assistant", "content": llm_response})
                self.memory.append({"role": "assistant", "content": llm_response})
                return llm_response

        except Exception as e:
            self.logger.error(f"Error processing response: {e}")
            raise RuntimeError(f"Response processing failed: {e}") from e



logger=logging.getLogger("ChatSession")

async def initialize_session(user_ip: str) -> ChatSession:
    """Initialize and return the chat session."""
    MCP_SERVER_CONFIGURATOIN = "app/mcp_client/servers_config.json"
    config = Configuration()  # Assumes Configuration class exists
    server_config = config.load_server_config(MCP_SERVER_CONFIGURATOIN)
    servers = [
        Server(name, srv_config)
        for name, srv_config in server_config["mcpServers"].items()
    ]
    llm_providers = get_providers()
    llm_client = LLMClient(llm_providers)
    logger.info('Initializng session')
    chat_session = await ChatSession.create(servers, llm_client, user_ip)
    return chat_session



def get_providers():
    """Retrieve registered LLM providers from llm_config.json."""
    provider_registry = {}
    llm_config = LLMConfiguration().data
    for provider_name, provider_config in llm_config["providers"].items():
        if provider_name == "gemini": provider_class = GeminiProvider(GEMINIConfig(provider_config))
        elif provider_name == "openai": provider_class = OpenAIProvider(OPENAIConfig(provider_config))
        else: raise ValueError(f"Unknown provider: {provider_name}")
        provider_registry[provider_name] = provider_class
    return provider_registry
