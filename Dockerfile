FROM python:3.11-slim

WORKDIR /app

COPY fetch_15min_spot_costs.py min_cost.py plot_spot_prices.py ./

RUN pip install --no-cache-dir matplotlib

ENV TZ=Europe/Helsinki

ENTRYPOINT ["python", "min_cost.py"]
