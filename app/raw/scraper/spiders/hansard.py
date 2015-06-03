# -*- coding: utf-8 -*-
from scrapy.spider import Spider, Request
from scrapy.selector import Selector
from scrapy import log

from raw.scraper.items import HansardAgenda, HansardMinutes, HansardRecord

import urlparse
import re

"""
Brief explanation:
e.g. http://www.legco.gov.hk/general/english/counmtg/yr12-16/mtg_1415.htm
The Hansard Spider, 'council_hansard', scrape 3 major types of documents: Agenda, Minutes, and Record.

A meeting can span over a couple of days.

For a meeting:
The agenda is unique and known ahead of meetings, so it is the earliest document available.
On LegCo it is a .htm page. Note there is a pdf version in LegCo Library, which should be identical in content (not checked).

The Minutes is a unique, short summary of a meeting. It contains vote results (as appendix) and other information.
Note that there is a nice XML file to vote records on webpage.
It should be available shortly after a meeting.
At the moment, I (lpounng) do not see any urgency in parsing this document - many of them contain images,
and the vote records can be accessed as nice XML.
A low priority to parse them is noted for now.

The Record holds what exactly happens during the meeting, and is thus most important.
The earliest one observed was date back to 1985-86. All of them are nice text pdf.
(PDF saved the day? You gotta be kidding me...)
Records usually consists of 2 versions: 'Floor'(即場紀錄本) and 正式紀錄(Floor version translated into 2 languages). 
See processors.hansard for more info.
Note that since a meeting can span over a couple of days, there can be more than 1 record for a meeting,
separated by date.
Usually, the Floor version is available first, followed by 正式紀錄, which takes time to be translated.

As side note, there is also a webcast link on some of the pages.
I (lpounng) have not dived into it yet.
"""

class HansardMixin:

    def parse(self, response):
        """ 
            Parse the handard record for a particular year
            Note that the term 'hansard' here is composed of 3 types of documents:
            Hansard Agenda, Hansard Minutes, and Hansard Records,
            while Hansard Records are further classified into
            
            Pages from 2000 onward
            Example: http://www.legco.gov.hk/general/english/counmtg/yr12-16/mtg_1314.htm
            
            Pages for 1998-2000
            http://www.legco.gov.hk/yr99-00/english/counmtg/general/counmtg.htm
            Looks very much like the ones from 2000 on, just a small difference in table index
            
            ---------------------------Line of divine--------------------------------------
            ---------------lpounng will not touch hansard before 1997 for now--------------
            
            Pages from 1994-1997
            Example: http://www.legco.gov.hk/yr95-96/english/lc_sitg/general/yr9596.htm
            Currently unsupported
            
            Pages from 1994 and before
            Example: http://www.legco.gov.hk/yr94-95/english/lc_sitg/yr9495.htm
            Currently unsupported?
        """
        sel = Selector(response)    

        # First find out what format we are dealing with
        # leave out pre-1998 hansard for the moment
        if sel.xpath("//table//td/strong[starts-with(text(),'Meetings')]"):
            self.log("%s: HANSARD - Post 1998 Hansard" % response.url, level=log.INFO)
            return self.parse_hansard_post_1998(response)
        elif sel.xpath("//table//td/strong[starts-with(text(),'Hansard')]"):
            pass
            #self.log("%s: HANSARD - Pre 1995 Hansard" % response.url, level=log.INFO)
            #return self.parse_hansard_pre_1995(response)
        elif sel.xpath("//h2[starts-with(text(),'LegCo Sittings')]"):
            pass
            #self.log("%s: HANSARD - 1995 - 1997 Hansard" % response.url, level=log.INFO)
            #self.log("%s: Page type not currently supported" % response.url, level=log.WARNING)
            #return self.parse_hansard_1995_to_1997(response)
        else:
            raise Exception("Unknown Hansard page type")

    def parse_hansard_1995_to_1997(self, response):
        #Currently unsupported
        #sel = Selector(response)    
        return None
        
    def parse_hansard_pre_1995(self, response):
        #Currently unsupported
        sel = Selector(response)    
        current_year = ""

        table = sel.xpath("//div[@id='_content_']/ul/table")[0]
        rows = table.xpath(".//tr")
        for entry in rows:
            cells = entry.xpath(".//td")

            # Year is sometimes defined, when a new year starts
            year_str = cells[0].xpath(".//strong/text()").extract()
            if year_str:
                if year_str[0].strip():
                    current_year = year_str[0].strip()
            
            # Month is always defined
            month_str = cells[1].xpath(".//strong/text()").extract()[0].strip()

            for cell in cells[2:]:
                day_str = cell.xpath(".//a/text()").extract()[0].strip()

                date_info = "%s %s %s" % (day_str, month_str, current_year)
        
                # PDF Url
                hansard_url = cell.xpath('.//a/@href').extract()[0]
                absolute_url = urlparse.urljoin(response.url, hansard_url.strip())
                hansard_record = HansardRecord()
                hansard_record['date'] = date_info
                hansard_record['language'] = 'en' # Only english for these records
                hansard_record["file_urls"] = [absolute_url]
                hansard_record['source_url'] = response.url
                yield hansard_record
            

    def parse_hansard_post_1998(self, response):
        # Remember that the page for yr99-00 is slightly different from those of 2000 on
        sel = Selector(response)    

        # Get the year that this index page is for
        # Meetings (Year 2013 - 2014)
        # This is mostly for debugging purposes so we can spit this out in the logs
        year_range = sel.xpath('//strong/em/text()').extract()
        if not year_range:
            self.log("%s: Could not find year range on hansard index page" % response.url, level=log.WARNING)
            return
        else:
            self.log("%s: Parsing Hansard Index: %s" % (response.url, year_range), level=log.INFO)

        # Find any dates at the top of this page. Other dates are identical
        # to this page, and indeed the current page will also be included in
        # the date list. Scrapy will prevent us recursing back into ourselves.
    
#         year_urls = sel.xpath('//tr/td/a[contains(@href,"#toptbl")]/@href').extract()
#         for year_url in year_urls:
#             absolute_url = urlparse.urljoin(response.url, year_url.strip())
#             req = Request(absolute_url, callback = self.parse)
#             yield req
        
        # We are looking for table rows which link to Hansard entries for a
        # particular date. In newer versions these are 6-columned table rows
        # where column 6 is a link to a webcast (doesn't seem to exist)
        # Older revisions are 5 columned rows. These are all after the anchor
        # 'hansard'.

        print("Parsing Rows")
        # Find the handsard table
        # note the format is different in year99-00
        
        #lpounng: I cannot find where this one applies - no @class='table_overflow'
        table = sel.xpath("//div[@class='table_overflow']//a[@name='hansard']/following::table[1]")
        
        if not table:
            # post-2000
            # http://www.legco.gov.hk/general/english/counmtg/yr08-12/mtg_0910.htm
            table = sel.xpath("//div[@id='_content_']//a[@name='hansard']/following::table[3]")
            
        if not table:
            # 1998-2000
            # http://www.legco.gov.hk/yr99-00/english/counmtg/general/counmtg.htm
            table = sel.xpath("//div[@id='_content_']//a[@name='hansard']/following::table[1]")
            
        rows = table.xpath(".//tr[count(td)>=5]")
        if not rows:
            self.log("%s: Could not find any Hansard entries to crawl into" % response.url, level=log.WARNING)
            return
    
        self.log("%s: %i rows found" % (response.url, len(rows)), level=log.INFO)

        for row in rows:
            date_info = ' '.join(row.xpath('.//td[1]/node()/text()').extract())
            self.log("%s: Row: %s" % (response.url, date_info), level=log.INFO)

            # Recurse into the agenda, if it exists
            agenda_url = row.xpath('.//td[2]/a/@href').extract()
            if agenda_url:
                absolute_url = urlparse.urljoin(response.url, agenda_url[0].strip())
                req = Request(absolute_url, callback = self.parse_hansard_agenda,meta={'date_info':date_info})
                yield req
            else:
                self.log("%s: Could not find an agenda URL for %s" % (response.url, date_info), level=log.WARNING)
        
            # Download the minutes document if it exists
            # We will access voting results via API, so no need to download them
            minutes_url = row.xpath('.//td[3]/a/@href').extract()
            if minutes_url:
                absolute_url = urlparse.urljoin(response.url, minutes_url[0].strip())
                minutes = HansardMinutes()
                minutes['date'] = date_info
                minutes['file_urls'] = [absolute_url]
                minutes['source_url'] = response.url
                yield minutes
            else:
                self.log("%s: Could not find an minutes URL for %s" % (response.url, date_info), level=log.WARNING)
            
            # Download Hansard records as pdf
            for (lang, index) in [('en',4),('cn',5)]:

                hansard_urls = row.xpath('.//td[%i]/a/@href' % index).extract()
                for url in hansard_urls:
                    # Is this a PDF entry, or do we need to recurse?
                    absolute_url = urlparse.urljoin(response.url, url.strip())
                    if absolute_url.endswith('pdf'):
                        hansard_record = HansardRecord()
                        hansard_record['date'] = date_info
                        hansard_record['language'] = lang
                        hansard_record['file_urls'] = [absolute_url]
                        hansard_record['source_url'] = response.url
                        yield hansard_record
                    else:
                        # Recurse into the HTML handler for the HTML Handard Record Index
                        # This applies to records in new interface
                        req = Request(absolute_url, callback = self.parse_hansard_html_record, meta={'date_info': date_info})
                        yield req

                if not hansard_urls:
                    self.log("%s: Could not find an hansard URL for %s, lang %s" % (response.url, date_info, lang), level=log.WARNING)


    def parse_hansard_agenda(self, response):
        # http://www.legco.gov.hk/yr13-14/english/counmtg/agenda/cm20131009.htm
        # Needs to be completed, large amount of HTML to parse. For now this is
        # just a stub.
        # So there are 2 places where agendas are placed: LegCo Library (as PDF/DOC),
        # and HTM files here  
        # We should think rather we need all 2 versions, or stick to a easier one.
        # In addition, the RawCouncilQuestion model has the questions and replies well handled,
        # so it is easy to cross-reference to/validate the ones in agendas.
        sel = Selector(response)    
        agenda = HansardAgenda()
        # note the date below comes from agenda htm page, not the same as minutes and records
        #agenda['date'] = u' '.join(sel.xpath('//h3[1]/text()').extract())
        #This date is same as others
        agenda['date'] = response.meta['date_info']
        agenda['file_urls'] = [response.url]
        agenda['source_url'] = response.url
        yield agenda
        

    def parse_hansard_html_record(self, response):
        # http://www.legco.gov.hk/php/hansard/english/rundown.php?date=2014-01-16&lang=0
        #
        # The HTML record is just an index into a PDF file, and doesn't
        # contain any extra information in itself. We find the PDF link
        # and then download.
        # 
        # <script type="text/javascript">
        #   var HansardID = 12;
        #   var Section = "MEETING SECTIONS";
        #   var PdfLink = "/yr13-14/english/counmtg/hansard/cm0116-translate-e.pdf\\#";
        #
        sel = Selector(response)    
    
        link_re = re.compile('var PdfLink = "(?P<pdf_url>[^\"]+)"')
        config_script = sel.xpath('//script[contains(text(),"PdfLink")]/text()')
        pdf_script_text = config_script.extract()[0]
        match = link_re.search(pdf_script_text)
        pdf_url = match.groupdict()['pdf_url'] 
        pdf_url = pdf_url.replace('\\\\#','')
        print "PDF URL", pdf_url
        absolute_url = urlparse.urljoin(response.url, pdf_url.strip())
        
        hr = HansardRecord()
        hr['date'] = response.meta['date_info']
        hr['file_urls'] = [absolute_url]
        hr['source_url'] = response.url
        yield hr


class HansardSpider(HansardMixin,Spider):
    name = 'council_hansard'
    start_urls = [
                  #scrape the English pages only. We can easily get the Chinese version by modifying links.
                  # see http://www.legco.gov.hk/general/chinese/timeline/council_meetings.htm for all.
                  
                  #第五屆立法會 (2012至2016年) (今屆 as of 2015)
                  'http://www.legco.gov.hk/general/english/counmtg/yr12-16/mtg_1415.htm',
                  #cannot handle Chinese yet.
                  #'http://www.legco.gov.hk/general/chinese/counmtg/yr12-16/mtg_1415.htm'
                  
                  #'http://www.legco.gov.hk/general/english/counmtg/yr12-16/mtg_1314.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr12-16/mtg_1213.htm'
                  # 第四屆立法會 (2008至2012年)
                  #'http://www.legco.gov.hk/general/english/counmtg/yr08-12/mtg_1112.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr08-12/mtg_1011.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr08-12/mtg_0910.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr08-12/mtg_0809.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr08-12/mtg_special.htm',
                  # 第三屆立法會 (2004至2008年)
                  #'http://www.legco.gov.hk/general/english/counmtg/yr04-08/mtg_0708.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr04-08/mtg_0607.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr04-08/mtg_0506.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr04-08/mtg_0405.htm',
                  # 第二屆立法會 (2000至2004年)
                  #'http://www.legco.gov.hk/general/english/counmtg/yr00-04/mtg_0304.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr00-04/mtg_0203.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr00-04/mtg_0102.htm',
                  #'http://www.legco.gov.hk/general/english/counmtg/yr00-04/mtg_0001.htm',
                  # 首屆立法會 (1998至2000年) 
                  #'http://www.legco.gov.hk/yr99-00/english/counmtg/general/cou_mtg.htm',
                  #'http://www.legco.gov.hk/yr98-99/english/counmtg/general/cou_mtg.htm',
                  ]
    
    
# Webpage for Hansard objects before 5/98 follows different formats.

class ProvisionalHansardSpider(HansardMixin,Spider):
    """
    Spider specially for the Provisional Legislative council (1997-1998)
    http://www.legco.gov.hk/yr97-98/english/counmtg/general/yr9798.htm
    """
    name = 'provisional_council_hansard'
    pass
 
class OldHansardSpider(HansardMixin,Spider):
    """
    Spider for Former Legco (before 7/1997)
    http://www.legco.gov.hk/yr97-98/english/former/lc_sitg.htm
    """
    name = 'old_council_hansard'
    pass