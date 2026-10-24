# SPDX-License-Identifier: MIT
FROM registry.vallery.net/jvallery/weather-python-base@sha256:099ee58eb685a16faff6f87a2541d20a56142de087ab317906a5affe9c5f0acd AS ffmpeg
# The fixed Debian snapshot pins the entire compiler/libx264 dependency closure.
# Include the matching security/updates suites: the pinned base already contains security fixes.
RUN printf 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261023T000000Z bookworm main\ndeb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261023T000000Z bookworm-updates main\ndeb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20261023T000000Z bookworm-security main\n' > /etc/apt/sources.list \
 && rm -f /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends build-essential nasm pkg-config libx264-dev curl xz-utils ca-certificates
WORKDIR /src
RUN curl --fail --location https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz -o ffmpeg.tar.xz \
 && echo '8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e  ffmpeg.tar.xz' | sha256sum -c - \
 && tar -xf ffmpeg.tar.xz \
 && cd ffmpeg-9.0.2 \
 && ./configure --prefix=/opt/ffmpeg --enable-gpl --enable-libx264 --disable-debug --disable-doc --disable-ffplay \
 && make -j2 && make install \
 && mkdir -p /opt/ffmpeg/lib /opt/ffmpeg/share/licenses \
 && cp /usr/lib/x86_64-linux-gnu/libx264.so.* /opt/ffmpeg/lib/ \
 && cp COPYING.GPLv2 COPYING.GPLv3 LICENSE.md /opt/ffmpeg/share/licenses/ \
 && cp /usr/share/doc/libx264-164/copyright /opt/ffmpeg/share/licenses/libx264-copyright

FROM registry.vallery.net/jvallery/weather-python-base@sha256:099ee58eb685a16faff6f87a2541d20a56142de087ab317906a5affe9c5f0acd AS dependencies
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: --target=/opt/python -r requirements.lock

FROM registry.vallery.net/jvallery/weather-python-base@sha256:099ee58eb685a16faff6f87a2541d20a56142de087ab317906a5affe9c5f0acd
ENV PYTHONPATH=/opt/python:/app/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/opt/ffmpeg/bin:$PATH LD_LIBRARY_PATH=/opt/ffmpeg/lib PORT=8000 METRICS_PORT=9000 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
COPY --from=dependencies /opt/python /opt/python
COPY --from=ffmpeg /opt/ffmpeg /opt/ffmpeg
WORKDIR /app
COPY app/weather app/weather
COPY app/migrations app/migrations
COPY alembic.ini ./
COPY scripts/weather /usr/local/bin/weather
RUN chmod 0755 /usr/local/bin/weather
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY docs/operations/ffmpeg.md ./FFMPEG.md
COPY supply-chain/capture-decoder ./supply-chain/capture-decoder
COPY docs/decisions/capture-decoder-reuse.md ./CAPTURE-DECODER.md
USER 10001:10001
EXPOSE 8000 9000
CMD ["python", "-m", "weather.cli", "api"]
