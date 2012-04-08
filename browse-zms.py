#!/usr/bin/python
#
# Small client for sending text to a socket and displaying the result.
#

# Licensed under the MIT license
# http://opensource.org/licenses/mit-license.php

# Copyright 2005, Tim Potter <tpot@samba.org>

from twisted.internet import reactor, error
from twisted.internet.protocol import Protocol, ClientFactory

class Send(Protocol):
	def connectionMade(self):
		self.transport.write('''POST /ContentDirectory/control HTTP/1.1\r
Host: 192.168.126.1:80\r
User-Agent: POSIX, UPnP/1.0, Intel MicroStack/1.0.1423\r
SOAPACTION: "urn:schemas-upnp-org:service:ContentDirectory:1#Browse"\r
Content-Type: text/xml\r
Content-Length: 511\r
\r
\r
<?xml version="1.0" encoding="utf-8"?>\r
  <s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">\r
    <s:Body>\r
      <u:Browse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">\r
        <ObjectID>0\OnlineMedia\Internet radio\</ObjectID>\r
        <BrowseFlag>BrowseDirectChildren</BrowseFlag>\r
        <Filter>*</Filter>\r
        <StartingIndex>0</StartingIndex>\r
        <RequestedCount>7</RequestedCount>\r
        <SortCriteria></SortCriteria>\r
      </u:Browse>\r
    </s:Body>\r
  </s:Envelope>\r\n''')

	def dataReceived(self, data):
		print(data)

	def connectionLost(self, reason):
		if reason.type != error.ConnectionDone:
			print str(reason)
		reactor.stop()

class SendFactory(ClientFactory):
	protocol = Send

host = '192.168.126.128'
port = 5643

reactor.connectTCP(host, port, SendFactory())
reactor.run()
