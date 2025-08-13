FROM ghcr.io/prefix-dev/pixi:0.50.2-bookworm-slim

WORKDIR /app

# COPY pixi.toml .
COPY . .

RUN pixi install

CMD ["pixi", "run", "start"]
