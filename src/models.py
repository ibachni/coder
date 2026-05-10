import os
from langchain.chat_models import init_chat_model

os.environ["OPENAI_API_KEY"] = "sk-..."
os.environ["ANTHROPIC_API_KEY"] = "sk-..."
os.environ["OPENROUTER_API_KEY"] = "sk-..."