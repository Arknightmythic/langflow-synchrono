FROM nginx:1.27-alpine

COPY docker/40-synchrono-ui.sh /docker-entrypoint.d/40-synchrono-ui.sh
COPY web/ /usr/share/nginx/html/
RUN rm -f /etc/nginx/conf.d/default.conf \
 && chmod 0755 /docker-entrypoint.d/40-synchrono-ui.sh

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1/ui-health >/dev/null || exit 1
