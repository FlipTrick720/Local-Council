# Image for the code mode's sandbox (app/sandbox.py): Python with the usual data libraries, nothing else.
# Build once, as the user the app runs as (needs internet only for this step):
#   docker build -t local-council-sandbox:1 deploy/sandbox
# The app starts every run with --network none, a read-only root, CPU/memory/process limits and user nobody.
FROM python:3.12-slim
RUN pip install --no-cache-dir numpy pandas scipy sympy matplotlib \
 && MPLCONFIGDIR=/opt/mpl python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot" \
 && chmod -R a+rX /opt/mpl
USER 65534:65534
