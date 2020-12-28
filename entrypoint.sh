#!/bin/bash

WORKDIR=$PWD
DATADIR=$WORKDIR/data

if [ "x$USE_S3_BUCKET" != "x" ]; then
    mkdir /data
    s3fs $USE_S3_BUCKET /data
    DATADIR=/data
fi

python $@ -w $DATADIR
