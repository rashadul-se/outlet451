FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FLASK_APP=app:create_app
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -r -u 10001 app && chown -R app /srv
USER 10001
EXPOSE 8000
# 1000 orders/day is light; 3 workers x 4 threads is ample headroom
CMD ["sh","-c","[ \"$SEED\" = 1 ] && flask seed; exec gunicorn -w 3 --threads 4 -b 0.0.0.0:${PORT:-8000} 'app:create_app()'"]
