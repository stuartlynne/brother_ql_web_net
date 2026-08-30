FROM python:3-alpine

RUN apk add fontconfig ttf-dejavu

WORKDIR /app

COPY . .

RUN pip install .

EXPOSE 8013

CMD [ "brother_ql_web_net" ]
