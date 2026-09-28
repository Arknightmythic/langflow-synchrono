# Tanpa satu pun dependency: seluruhnya pustaka standar Python.
# Image-nya kecil dan tidak ada yang perlu di-pin.
FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1
COPY pengukur.py /pengukur.py
EXPOSE 9200
CMD ["python", "/pengukur.py"]
