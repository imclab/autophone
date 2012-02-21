# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re
import os
import threading
import androidutils
import ConfigParser
import json
import urllib2
from time import sleep
from phonetest import PhoneTest
from devicemanagerSUT import DeviceManagerSUT

class S1S2Test(PhoneTest):

    def __init__(self, phone_cfg, config_file=None, status_cb=None):
        PhoneTest.__init__(self, phone_cfg, config_file, status_cb)
        
    def runjob(self, job):
        if 'buildurl' not in job or 'androidprocname' not in job or \
                'revision' not in job or 'blddate' not in job or \
                'bldtype' not in job or 'version' not in job:
            self.logger.error('Invalid job configuration: %s' % job)
            raise NameError('ERROR: Invalid job configuration: %s' % job)

        if not androidutils.install_build_adb(phoneid=self.phone_cfg['phoneid'],
                                              url=job['buildurl'],
                                              procname=job['androidprocname'],
                                              serial=self.phone_cfg['serial']):
            return

        # Read our config file which gives us our number of
        # iterations and urls that we will be testing
        self.prepare_phone(job)

        self.dm = DeviceManagerSUT(self.phone_cfg['ip'],
                                   self.phone_cfg['sutcmdport'])

        intent = job['androidprocname'] + '/.App'

        for testname,url in self._urls.iteritems():
            self.logger.info('%s: Running test %s for %s iterations' %
                             (self.phone_cfg['phoneid'], testname,
                              self._iterations))
            for i in range(self._iterations):
                # Set status
                self.set_status(msg='Run %s for url %s' % (i,url))

                # Clear logcat
                androidutils.run_adb('logcat', ['-c'], self.phone_cfg['serial'])

                # Get start time
                try:
                    starttime = self.dm.getInfo('uptimemillis')['uptimemillis'][0]
                except IndexError:
                    starttime = 0

                # Run test
                androidutils.run_adb('shell',
                                     ['sh', '/mnt/sdcard/s1test/runbrowser.sh', intent,
                                      url], self.phone_cfg['serial'])

                # Let browser stabilize - this was 5s but that wasn't long
                # enough for the device to stabilize on slow devices
                sleep(10)

                # Get results
                throbberstart, throbberstop, drawtime = self.analyze_logcat()

                # Publish results
                self.publish_results(starttime=int(starttime),
                                     tstrt=throbberstart,
                                     tstop=throbberstop,
                                     drawing=drawtime,
                                     job=job,
                                     testname=testname)
                androidutils.kill_proc_sut(self.phone_cfg['ip'],
                                           self.phone_cfg['sutcmdport'],
                                           job['androidprocname'])
                androidutils.remove_sessionstore_files_adb(
                    self.phone_cfg['serial'],
                    procname=job['androidprocname'])

    def prepare_phone(self, job):
        androidutils.run_adb('shell', ['mkdir', '/mnt/sdcard/s1test'],
                             self.phone_cfg['serial'])
        androidutils.run_adb('push', ['runbrowser.sh', '/mnt/sdcard/s1test/'],
                             self.phone_cfg['serial'])

        testroot = '/mnt/sdcard/s1test'
        
        if not os.path.exists(self.config_file):
            self.logger.error('Cannot find config file: %s' % self.config_file)
            raise NameError('Cannot find config file: %s' % self.config_file)
        
        cfg = ConfigParser.RawConfigParser()
        cfg.read(self.config_file)
        
        # Map URLS - {urlname: url} - urlname serves as testname
        self._urls = {}
        for u in cfg.items('urls'):
            self._urls[u[0]] = u[1]

        # Move the local html files in htmlfiles onto the phone's sdcard
        # Copy our HTML files for local use into place
        # TODO: Handle errors       
        for h in cfg.items('htmlfiles'):
            androidutils.run_adb('push', [h[1], testroot + '/%s' % 
                                          os.path.basename(h[1])],
                                 self.phone_cfg['serial'])
        
        self._iterations = cfg.getint('settings', 'iterations')
        self._resulturl = cfg.get('settings', 'resulturl')
 
    def analyze_logcat(self):
        buf = androidutils.run_adb('logcat', ['-d'], self.phone_cfg['serial'])
        buf = buf.split('\r\n')
        throbberstartRE = re.compile('.*Throbber start$')
        throbberstopRE = re.compile('.*Throbber stop$')
        endDrawingRE = re.compile('.*endDrawing$')
        throbstart = 0
        throbstop = 0
        enddraw = 0

        for line in buf:
            line = line.strip()
            if throbberstartRE.match(line):
                throbstart = line.split(' ')[-4]
            elif throbberstopRE.match(line):
                throbstop = line.split(' ')[-4]
            elif endDrawingRE.match(line):
                enddraw = line.split(' ')[-3]
        return (int(throbstart), int(throbstop), int(enddraw))

    def publish_results(self, starttime=0, tstrt=0, tstop=0, drawing=0, job=None, testname = ''):
        msg = 'Start Time: %s Throbber Start: %s Throbber Stop: %s EndDraw: %s' % (starttime, tstrt, tstop, drawing)
        print 'RESULTS %s:%s' % (self.phone_cfg['phoneid'], msg)
        self.logger.info('RESULTS: %s:%s' % (self.phone_cfg['phoneid'], msg))
        
        # Create JSON to send to webserver
        resultdata = {}
        resultdata['phoneid'] = self.phone_cfg['phoneid']
        resultdata['testname'] = testname
        resultdata['starttime'] = starttime
        resultdata['throbberstart'] = tstrt
        resultdata['throbberstop'] = tstop
        resultdata['enddrawing'] = drawing
        resultdata['blddate'] = job['blddate']
        
        resultdata['revision'] = job['revision']
        resultdata['productname'] = job['androidprocname']
        resultdata['productversion'] = job['version']
        resultdata['osver'] = self.phone_cfg['osver']
        resultdata['bldtype'] = job['bldtype']
        resultdata['machineid'] = self.phone_cfg['machinetype']
        
        # Upload
        result = json.dumps({'data': resultdata})
        req = urllib2.Request(self._resulturl, result,
                              {'Content-Type': 'application/json'})
        f = urllib2.urlopen(req)
        response = f.read()
        f.close()
