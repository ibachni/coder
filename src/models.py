import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()

openai_api_key = os.getenv("OPENAI_API_KEY")
gpt_nano_model = init_chat_model("gpt-5.4-nano")
