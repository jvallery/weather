# SPDX-License-Identifier: MIT
FROM registry.vallery.net/jvallery/weather-python-base@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS ffmpeg
# The fixed Debian snapshot pins the entire compiler/libx264 dependency closure.
# Include the matching security/updates suites: the pinned base already contains security fixes.
RUN printf 'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261117T000000Z trixie main\ndeb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261117T000000Z trixie-updates main\ndeb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20261117T000000Z trixie-security main\n' > /etc/apt/sources.list \
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

FROM registry.vallery.net/jvallery/weather-python-base@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS dependencies
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: --target=/opt/python -r requirements.lock

FROM registry.vallery.net/jvallery/weather-python-base@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
ENV PYTHONPATH=/opt/python:/app/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH=/opt/ffmpeg/bin:$PATH LD_LIBRARY_PATH=/opt/ffmpeg/lib PORT=8000 METRICS_PORT=9000
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
# The pinned base still ships zlib1g 1:1.3.dfsg+really1.3.1-1+b1 (CVE-2026-85091, HIGH, fixed in +deb13u1).
# Upgrade only that package and fail the build unless the fixed version is installed (#213, AMC D-880).
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
 && rm -rf /var/lib/apt/lists/* \
 && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' zlib1g)" ge '1:1.3.dfsg+really1.3.1-1+deb13u1'
# pip is build-time only; its vendored urllib3/msgpack/setuptools carried fixable Highs at runtime (#213).
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
    /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14
USER 10001:10001
EXPOSE 8000 9000
CMD ["python", "-m", "weather.cli", "api"]
