FROM ubuntu:20.04

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        build-essential \
        g++ \
        gdb \
        valgrind \
        vim \
        python3 \
        && rm -rf /var/lib/apt/lists/*

ENV LD_BIND_NOW=1
WORKDIR /work