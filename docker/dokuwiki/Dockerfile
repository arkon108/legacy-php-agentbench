FROM php:8.2-cli

RUN apt-get update && apt-get install -y --no-install-recommends \
        git unzip libicu-dev libbz2-dev libpng-dev libgd-dev libsqlite3-dev \
    && docker-php-ext-install intl bz2 gd pdo_sqlite pcntl \
    && rm -rf /var/lib/apt/lists/*

RUN echo 'pcre.jit=0' > "$PHP_INI_DIR/conf.d/pcre.ini"

COPY docker/dokuwiki/prepend.php /usr/local/etc/php/prepend.php
RUN echo 'auto_prepend_file=/usr/local/etc/php/prepend.php' > "$PHP_INI_DIR/conf.d/prepend.ini"

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /dokuwiki

COPY vendor/dokuwiki/ .

WORKDIR /dokuwiki/_test

RUN composer install --no-interaction --no-progress --prefer-dist

WORKDIR /dokuwiki

# Match CI: undefined-variable was promoted to E_WARNING in PHP 8.2;
# DokuWiki's phpunit.xml only suppresses E_NOTICE conversions.
RUN sed -i 's/convertNoticesToExceptions="false"/convertNoticesToExceptions="false" convertWarningsToExceptions="false" convertDeprecationsToExceptions="false"/' _test/phpunit.xml \
    && sed -i 's/<group>flaky<\/group>/<group>flaky<\/group>\n            <group>internet<\/group>/' _test/phpunit.xml

CMD ["php", "_test/vendor/bin/phpunit", "--configuration", "_test/phpunit.xml", "--verbose", "--stderr"]
