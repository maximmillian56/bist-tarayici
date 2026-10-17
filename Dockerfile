FROM python:3.10-slim

WORKDIR /app

# Katman önbelleği için önce gereksinimleri kur
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama dosyalarını kopyala (.dockerignore gereksiz dosyaları hariç tutar)
COPY . /app/

# Varsayılan port (Hugging Face Spaces için 7860, yerel/Render için 5000 veya PORT env)
ENV PORT=5000
EXPOSE 5000 7860

CMD ["python", "app.py"]
