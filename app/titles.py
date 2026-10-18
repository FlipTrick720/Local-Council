"""Short chat titles, written by a model from the first question and answer."""
import re

from app.ollama import OllamaClient

PROMPT = (
    "Write a short title (2 to 6 words) for the following conversation, in the language of the user's message. "
    "Reply with the title only: no quotes, no trailing punctuation, no explanation."
)


async def generate(client: OllamaClient, model: str, question: str, answer: str) -> str:
    conversation = f"User: {question[:1500]}\n\nAssistant: {answer[:1500]}"
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": conversation}]
    parts = []
    # think=False: a reasoning model would otherwise think at length about a five-word title.
    async for chunk in client.chat_stream(model, messages, {"temperature": 0.2, "num_predict": 40}, think=False):
        if chunk["type"] == "content":
            parts.append(chunk["text"])
    return clean("".join(parts))


def clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    line = next((line for line in text.strip().splitlines() if line.strip()), "")
    line = re.sub(r"^(title|titel)\s*:\s*", "", line.strip(), flags=re.I)
    return line.strip(" \"'„“”«»*#`.")[:60]
