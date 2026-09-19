FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[backend]' && useradd --uid 10001 --create-home appuser && mkdir /data && chown appuser /data
USER appuser
ENV VIRTUAL_YOU_DATA_DIR=/data
EXPOSE 8000
CMD ["virtual-you-server", "serve", "--host", "0.0.0.0", "--port", "8000"]
