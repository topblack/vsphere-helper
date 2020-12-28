FROM python:buster

RUN apt-get update
RUN apt-get install s3fs -y

WORKDIR /app
ADD *.py .
ADD entrypoint.sh .
ADD requirements.txt .

RUN pip install -r requirements.txt
RUN chmod 777 entrypoint.sh

ENTRYPOINT ["/bin/bash", "./entrypoint.sh"]