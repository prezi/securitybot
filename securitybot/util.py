import pytz
import binascii
import os
import logging
import tzlocal
from datetime import datetime, timedelta
from collections import namedtuple

from securitybot.sql import SQLEngine
from scribe_logger.logger import ScribeLogHandler

__author__ = 'Alex Bertsch'
__email__ = 'abertsch@dropbox.com'


# http://stackoverflow.com/questions/36932/how-can-i-represent-an-enum-in-python
def enum(*sequential, **named):
    enums = dict(zip(sequential, range(len(sequential))), **named)
    return type('Enum', (), enums)


def tuple_builder(answer=None, text=None):
    tup = namedtuple('Response', ['answer', 'text'])
    tup.answer = answer if answer is not None else None
    tup.text = text if text is not None else ''
    return tup


OPENING_HOUR = 10
CLOSING_HOUR = 18
LOCAL_TZ = tzlocal.get_localzone()


def during_business_hours(time):
    '''
    Checks if a given time is within business hours. Currently is true
    from 10:00 to 17:59. Also checks to make sure that the day is a weekday.

    Args:
        time (Datetime): A datetime object to check.
    '''
    if time.tzinfo is not None:
        here = time.astimezone(LOCAL_TZ)
    else:
        here = time.replace(tzinfo=pytz.utc).astimezone(LOCAL_TZ)
    return (OPENING_HOUR <= here.hour < CLOSING_HOUR and
            1 <= time.isoweekday() <= 5)


def get_expiration_time(start, time):
    '''
    Gets an expiration time for an alert.
    Works by adding on a certain time and wrapping around after business hours
    so that alerts that are started near the end of the day don't expire.

    Args:
        start (Datetime): A datetime object indicating when an alert was started.
        time (Timedelta): A timedelta representing the amount of time the alert
            should live for.
    Returns:
        Datetime: The expiry time for an alert.
    '''
    if start.tzinfo is None:
        start = start.replace(tzinfo=pytz.utc)
    end = start + time
    if not during_business_hours(end):
        logging.debug('Not during business hours.')
        end_of_day = datetime(year=start.year,
                              month=start.month,
                              day=start.day,
                              hour=CLOSING_HOUR,
                              tzinfo=LOCAL_TZ)
        delta = end - end_of_day
        next_day = end_of_day + timedelta(hours=(OPENING_HOUR - CLOSING_HOUR) % 24)
        # This may land on a weekend, so march to the next weekday
        while not during_business_hours(next_day):
            next_day += timedelta(days=1)
        end = next_day + delta
    return end


def create_new_alert(title, ldap, description, reason, url=None, key=None, escalation_list=None):
    # type: (str, str, str, str, str, str) -> None
    '''
    Creates a new alert in the SQL DB with an optionally random hash.
    '''
    # Generate random key if none provided
    if key is None:
        key = binascii.hexlify(os.urandom(32))

    if url is None:
        # currently url field cannot be NULL
        url = ''

    # Insert that into the database as a new alert
    SQLEngine.execute('''
    INSERT INTO alerts (hash, ldap, title, description, reason, url, event_time)
    VALUES (UNHEX(%s), %s, %s, %s, %s, %s, NOW())
    ''', (key, ldap, title, description, reason, url))

    SQLEngine.execute('''
    INSERT INTO user_responses (hash, ldap, comment, performed, authenticated, updated_at)
    VALUES (UNHEX(%s), ldap, '', false, false, NOW())
    ''', (key,))

    if escalation_list is not None and isinstance(escalation_list, list):
        for escalation in escalation_list:
            SQLEngine.execute('INSERT INTO escalation (hash, ldap, delay_in_sec, escalated_at) VALUES (UNHEX(%s), %s, %s, NULL)',
                              (key, escalation.ldap, escalation.delay_in_sec))

    SQLEngine.execute('INSERT INTO alert_status (hash, status) VALUES (UNHEX(%s), 0)', (key,))

    logging.info("Created new alert: {}".format({
        'title': title,
        'ldap': ldap,
        'description': description,
        'reason': reason,
        'url': url,
        'escalation': escalation_list
    }))


def init_scribe_logging():
    scribe_host = os.getenv('SCRIBE_HOST')
    scribe_port = os.getenv('SCRIBE_PORT', 1463)
    scribe_category = os.getenv('SCRIBE_CATEGORY', 'securitybot')

    if scribe_host:
        environment = os.getenv('ENVIRONMENT', 'default')
        formatter = logging.Formatter('%(asctime)s ' + environment + ' %(name)s %(levelname)-8s %(message)s\n')
        scribe_handler = ScribeLogHandler(scribe_host, scribe_port, category=scribe_category)
        scribe_handler.setLevel(logging.DEBUG)
        scribe_handler.setFormatter(formatter)
        logging.getLogger().addHandler(scribe_handler)


def init_sentry_logging():
    sentry_dsn = os.getenv('SENTRY_DSN')
    if sentry_dsn:
        from raven import Client
        from raven.handlers.logging import SentryHandler
        from raven.conf import setup_logging
        sentry_client = Client(sentry_dsn, environment=os.getenv('ENVIRONMENT', 'default'))
        handler = SentryHandler(sentry_client, level=logging.WARNING)
        setup_logging(handler)
