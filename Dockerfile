# 1. Use an official Python image
FROM python:3.12-slim

# 2. Set the working directory inside the container
WORKDIR /code

# 3. Copy the requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy your /app folder into the container
COPY ./app ./app

# 5. Command to run the app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]