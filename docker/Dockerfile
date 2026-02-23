FROM alpine:latest

RUN apk add --no-cache openssh-server python3 iproute2 net-tools \
    && ssh-keygen -A \
    && mkdir -p /root/.ssh \
    && chmod 700 /root/.ssh

COPY sshd_config /etc/ssh/sshd_config
COPY test_key.pub /root/.ssh/authorized_keys

RUN chmod 600 /root/.ssh/authorized_keys

EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e"]
