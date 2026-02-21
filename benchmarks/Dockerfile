FROM panubo/sshd:latest

# Install python3, nginx, darkhttpd, and net-tools (for ss/netstat used by ssh-auto-forward)
RUN apk add --no-cache python3 nginx darkhttpd iproute2 net-tools

# Create test data directory
RUN mkdir -p /srv/bench/small

# Pre-generated test files on disk: 10 MB, 100 MB, 1 GB
RUN dd if=/dev/urandom of=/srv/bench/10mb.bin  bs=1M count=10   2>/dev/null
RUN dd if=/dev/urandom of=/srv/bench/100mb.bin bs=1M count=100  2>/dev/null
RUN dd if=/dev/urandom of=/srv/bench/1gb.bin   bs=1M count=1024 2>/dev/null

# Generate 1000 x 10 KB small HTML-like files (simulates many HTML docs)
RUN i=0; while [ "$i" -lt 1000 ]; do \
        printf '<html><body><h1>Page %d</h1>' "$i" > /srv/bench/small/page_${i}.html; \
        dd if=/dev/urandom bs=9800 count=1 2>/dev/null | base64 >> /srv/bench/small/page_${i}.html; \
        printf '</body></html>' >> /srv/bench/small/page_${i}.html; \
        i=$((i + 1)); \
    done

# Copy nginx config and streaming generator
COPY nginx-benchmark.conf /etc/nginx/nginx-benchmark.conf
COPY stream_generator.py /srv/bench/stream_generator.py

EXPOSE 22
