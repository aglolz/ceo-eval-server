# Pick ONE entry point per Railway deployment:
#
# Ankita's Test (all 10 judges):
# web: gunicorn ankita_server:app --bind 0.0.0.0:$PORT
#
# Maya's Test (all 10 judges):
# web: gunicorn maya_server:app --bind 0.0.0.0:$PORT
#
# CEO Live (production, no judges yet):
# web: gunicorn ceo_live_server:app --bind 0.0.0.0:$PORT

web: gunicorn ankita_server:app --bind 0.0.0.0:$PORT
