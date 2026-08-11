# Pick ONE entry point per Railway deployment:
#
# CEO Live (PRODUCTION — the live number routes here; all judges):
# web: gunicorn ceo_live_server:app --bind 0.0.0.0:$PORT --timeout 120
#
# Maya's Test (all 10 judges):
# web: gunicorn maya_server:app --bind 0.0.0.0:$PORT --timeout 120

web: gunicorn ceo_live_server:app --bind 0.0.0.0:$PORT --timeout 120
