#!/usr/bin/env python3
"""Create private credentials for a fresh bundled MariaDB/Redis installation."""
import argparse
import os
from pathlib import Path
import secrets

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('ctfd.env'))
    args = parser.parse_args()
    db_password, root_password, redis_password = (secrets.token_hex(32) for _ in range(3))
    values = {
        'SECRET_KEY': secrets.token_hex(32),
        'DATABASE_URL': 'mysql+pymysql://ctfd:' + db_password + '@database:3306/ctfd',
        'REDIS_URL': 'redis://:' + redis_password + '@cache:6379/0',
        'MARIADB_PASSWORD': db_password,
        'MARIADB_ROOT_PASSWORD': root_password,
        'REDIS_PASSWORD': redis_password,
        'CTFD_ADMIN_PASSWORD': secrets.token_hex(24),
    }
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error('Refusing to overwrite existing runtime credentials: ' + str(args.output))
    with os.fdopen(descriptor, 'w') as output:
        output.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
    print('Created private runtime credentials: ' + str(args.output))

if __name__ == '__main__':
    main()
