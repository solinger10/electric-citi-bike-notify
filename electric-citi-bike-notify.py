#!/usr/bin/env python

# Note: for setting up email with sendmail, see: http://linuxconfig.org/configuring-gmail-as-sendmail-email-relay

import argparse
import commands
import json
import logging
import smtplib
import sys
import os
import glob
import requests
import hashlib

from datetime import datetime
from os import path
from subprocess import check_output
from distutils.spawn import find_executable
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

EMAIL_TEMPLATE = """
<p>Electric Citi Bikes are available!</p>
%s
"""
STATION_STATUS_URL = 'https://gbfs.citibikenyc.com/gbfs/en/station_status.json'
STATION_INFORMATION_URL = 'https://gbfs.citibikenyc.com/gbfs/en/station_information.json'


def notify_send_email(stations_available, emails, settings):
    use_gmail = settings.get("use_gmail")
    sender = settings.get('email_from')

    try:
        if use_gmail:
            password = settings.get('gmail_password')
            if not password:
                logging.warning('Trying to send from gmail, but password was not provided.')
                return
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(sender, password)
        else:
            username = settings.get('email_username').encode('utf-8')
            password = settings.get('email_password').encode('utf-8')
            server = smtplib.SMTP(settings.get('email_server'), settings.get('email_port'))
            server.ehlo()
            server.starttls()
            server.ehlo()
            if username:
                    server.login(username, password)

        subject = "Alert: Electric Citi Bikes!"

        stations_information = requests.get(STATION_INFORMATION_URL).json()

        # parse the json
        if not stations_information:
            logging.info('Failed to get station information.')
            return

        station_info_by_id = {}
        for station in stations_information["data"]["stations"]:
            station_info_by_id[station["station_id"]] = station

        stations_html = '<ul>'
        for station_id, count in stations_available.items():
            station_info = station_info_by_id[station_id]
            stations_html += "<li>" + str(count) + " Electric Citi bike(s) at <a href='" + station_info["rental_url"] + "'>" + station_info["name"] + "</a></li>"

        stations_html += "</ul>"

        message = EMAIL_TEMPLATE % stations_html

        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = ','.join(emails)
        msg['mime-version'] = "1.0"
        msg['content-type'] = "text/html"
        msg.attach(MIMEText(message, 'html'))

        server.sendmail(sender, emails, msg.as_string())
        server.quit()
    except Exception:
        logging.exception('Failed to send succcess e-mail.')

def notify_osx(msg):
    commands.getstatusoutput("osascript -e 'display notification \"%s\" with title \"Global Entry Notifier\"'" % msg)

def notify_sms(settings, dates):
    for avail_apt in dates: 
        try:
            from twilio.rest import Client
        except ImportError:
            logging.warning('Trying to send SMS, but TwilioRestClient not installed. Try \'pip install twilio\'')
            return

        try:
            account_sid = settings['twilio_account_sid']
            auth_token = settings['twilio_auth_token']
            from_number = settings['twilio_from_number']
            to_number = settings['twilio_to_number']
            assert account_sid and auth_token and from_number and to_number
        except (KeyError, AssertionError):
            logging.warning('Trying to send SMS, but one of the required Twilio settings is missing or empty')
            return

        # Twilio logs annoyingly, silence that
        logging.getLogger('twilio').setLevel(logging.WARNING)
        client = Client(account_sid, auth_token)
        body = 'New GOES appointment available on %s' % avail_apt
        logging.info('Sending SMS.')
        client.messages.create(body=body, to=to_number, from_=from_number)

def main(settings, pwd):
    try:
        # obtain the json from the web url
        data = requests.get(STATION_STATUS_URL).json()

    	# parse the json
        if not data:
            logging.info('Failed to get station status.')
            return

        stations_with_ebikes = {}
        for station in data["data"]["stations"]:
            if station["num_ebikes_available"] > 0:
                stations_with_ebikes[station["station_id"]] = station["num_ebikes_available"]

        #print stations_with_ebikes

        file_name = pwd + "last_run_results.csv"
        print file_name
	if not os.path.exists(file_name):
            open(file_name, "a").close()
        file = open(file_name, "r+")
        old_results = file.readline()
        file.seek(0)
        file.truncate()
        file.write(",".join(stations_with_ebikes.keys()))
        file.close()
        old_results_list = old_results.split(",")

        print stations_with_ebikes
        stations_with_ebikes_keys = stations_with_ebikes.keys()
        for notify_config in settings.get("notifications"):
            stations_wanted_and_have_ebike = {stations_id: stations_with_ebikes[stations_id] for stations_id in notify_config["station_ids"] if stations_id in stations_with_ebikes_keys}
            print stations_wanted_and_have_ebike
            any_new_stations = False
            for station_id in stations_wanted_and_have_ebike:
                if station_id not in old_results_list:
                    any_new_stations = True

            print any_new_stations
            if any_new_stations:
                print "sending email to " + ",".join(notify_config["emails"])
                notify_send_email(stations_wanted_and_have_ebike, notify_config["emails"], settings)

    except OSError:
        logging.critical("Something went wrong")
        return


def _check_settings(config):
    required_settings = (
        'notifications',
        'logfile'
    )

    for setting in required_settings:
        if not config.get(setting):
            raise ValueError('Missing setting %s in config.json file.' % setting)

    if config.get('no_email') == False and not config.get('email_from'):
        raise ValueError('email_from required for sending email. (Run with --no-email or no_email=True to disable email.)')

    if config.get('use_gmail') and not config.get('gmail_password'):
        raise ValueError('gmail_password not found in config but is required when running with use_gmail option')

if __name__ == '__main__':

    # Configure Basic Logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(levelname)s: %(asctime)s %(message)s',
        datefmt='%m/%d/%Y %I:%M:%S %p',
        stream=sys.stdout,
    )

    pwd = path.dirname(sys.argv[0]) + "/"

    # Parse Arguments
    parser = argparse.ArgumentParser(description="Command line script to check for goes openings.")
    parser.add_argument('--no-email', action='store_true', dest='no_email', default=False, help='Don\'t send an e-mail when the script runs.')
    parser.add_argument('--use-gmail', action='store_true', dest='use_gmail', default=False, help='Use the gmail SMTP server instead of sendmail.')
    parser.add_argument('--config', dest='configfile', default='%s/config.json' % pwd, help='Config file to use (default is config.json)')
    arguments = vars(parser.parse_args())
    logging.info("config file is:" + arguments['configfile'])
    # Load Settings
    try:
        with open(arguments['configfile']) as json_file:
            settings = json.load(json_file)

            # merge args into settings IF they're True
            for key, val in arguments.iteritems():
                if not arguments.get(key): continue
                settings[key] = val

            settings['configfile'] = arguments['configfile']
            _check_settings(settings)
    except Exception as e:
        logging.error('Error loading settings from config.json file: %s' % e)
        sys.exit()

    # Configure File Logging
    if settings.get('logfile'):
        handler = logging.FileHandler('%s/%s' % (pwd, settings.get('logfile')))
        handler.setFormatter(logging.Formatter('%(levelname)s: %(asctime)s %(message)s'))
        handler.setLevel(logging.DEBUG)
        logging.getLogger('').addHandler(handler)

    logging.debug('Running cron with arguments: %s' % arguments)

    main(settings, pwd)
