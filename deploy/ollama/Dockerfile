FROM ollama/ollama:latest

ENV OLLAMA_HOST=0.0.0.0:11434
ENV OLLAMA_MODEL=qwen2.5:1.5b

COPY deploy/ollama/start-ollama.sh /usr/local/bin/start-ollama.sh

CMD ["sh", "/usr/local/bin/start-ollama.sh"]
