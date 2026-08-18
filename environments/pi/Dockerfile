# Prepared environment for Pi (pi-coding-agent) — https://github.com/badlogic/pi-mono
# Minimal coding harness by Mario Zechner; needs Node >= 22 (undici), pinned here.
FROM node:24-slim
RUN npm install -g @earendil-works/pi-coding-agent@0.84.2 && npm cache clean --force
ENV PI_NO_UPDATE_CHECK=1
WORKDIR /workspace
ENTRYPOINT ["pi"]
