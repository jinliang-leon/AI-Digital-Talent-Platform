FROM node:24-alpine
WORKDIR /app
COPY frontend ./
USER node
CMD ["node", "server.mjs"]
