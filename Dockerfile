FROM python:3.12-alpine3.21

# https://stackoverflow.com/questions/58701233/docker-logs-erroneously-appears-empty-until-container-stops
ENV PYTHONUNBUFFERED=1

# Define build argument with default value
ARG VERSION=dev
# Set it as an environment variable
ENV VERSION=$VERSION

COPY ./requirements.txt /requirements.txt
COPY ./supervisord.conf /etc/supervisord.conf
COPY ./nginx.conf /etc/nginx/nginx.conf

WORKDIR /yamtrack

RUN apk add --no-cache nginx shadow \
    && pip install --no-cache-dir setuptools supervisor==4.2.5 \
    && pip install --no-cache-dir -r /requirements.txt \
    && rm -rf /root/.cache /tmp/* \
    && find /usr/local -type d -name __pycache__ -exec rm -rf {} + \
    && useradd -U -M -s /bin/sh abc \
    && mkdir -p /var/log/nginx /var/lib/nginx/body

# Django app
COPY src ./

# Copy VERSION file to container root
COPY VERSION ./

# Copy and set permissions for entrypoint
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && sed -i 's/\r$//' /entrypoint.sh \
    && python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["/entrypoint.sh"]

HEALTHCHECK --interval=45s --timeout=15s --start-period=30s --retries=5 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:8000/health/ || exit 1
