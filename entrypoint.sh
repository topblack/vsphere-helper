#!/bin/bash

WORKDIR=$PWD
DATADIR=$WORKDIR/data

if [ "x$USE_S3_BUCKET" != "x" ]; then
    PASSFILE=${HOME}/.passwd-s3fs
    mkdir /data
    echo $AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY > ${PASSFILE}
    chmod 600 ${PASSFILE}
    s3fs $USE_S3_BUCKET /data -o passwd_file=${PASSFILE}
    DATADIR=/data
fi

python $@ -w $DATADIR
