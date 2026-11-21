FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     PORT=9999
WORKDIR /app
COPY proxy_core.py tunnel_core.py server.py /app/
EXPOSE 9999
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3   CMD python3 -c "import os,socket;socket.create_connection(('127.0.0.1',int(os.environ.get('PORT','9999')),5)).close()" || exit 1
CMD ["python3", "server.py"]
