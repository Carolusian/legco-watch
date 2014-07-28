#!/usr/local/bin/python
# -*- coding: utf-8 -*-

"""
Document wrappers for LegCo Agendas
"""
from collections import OrderedDict
import logging
import lxml
from lxml import etree
from lxml.html.clean import clean_html, Cleaner
import re
from lxml.html import HTMLParser
import itertools
from raw.utils import to_string, to_unicode, grouper


logger = logging.getLogger('legcowatch')
QUESTION_PATTERN_E = ur'^\*?([0-9]+)\..*?Hon\s(.*?)\sto ask:'
QUESTION_PATTERN_C = ur'^\*?([0-9]+)\.\s*(.*?)議員問:'
LEGISLATION_E = u'Subsidiary Legislation'
LEGISLATION_C = u'附屬法例'
OTHER_PAPERS_E = u'Other Papers'
OTHER_PAPERS_C = u'其他文件'


class CouncilAgenda(object):
    """
    Object representing the Council Agenda document.  This class
    parses the document source and makes all of the individual elements easily accessible
    """
    SECTION_MAP = OrderedDict(
        (
            ('tabled_papers', [u'Tabling of Paper', u'提交文件']),
            ('members_bills', [u"Members' Bill", u"Member's Bill", u'議員法案']),
            ('members_motions', [u"Members' Motion", u"Member's Motion", u'議員議案']),
            ('questions', [u'Question', u'質詢']),
            ('bills', [u'Bill', u'法案']),
            ('motions', [u'Motion', u'議案']),
        )
    )

    def __init__(self, uid, source, *args, **kwargs):
        self.uid = uid
        if uid[-1] == 'e':
            self.english = True
        else:
            self.english = False
        # Raw html string
        self.source = source
        self.tree = None
        self.tabled_papers = None
        self.questions = None
        self.motions = None
        self.bills = None
        self.members_bills = None
        self.members_motions = None
        self.other = None
        self._headers = []
        self._load()
        self._clean()
        self._parse()

    def __repr__(self):
        return u'<CouncilAgenda: {}>'.format(self.uid)

    def _load(self):
        """
        Load the ElementTree from the source
        """
        # Convert directional quotation marks to regular quotes
        double_quotes = ur'[\u201c\u201d]'
        self.source = re.sub(double_quotes, u'"', self.source)
        single_quotes = ur'[\u2019\u2018]'
        self.source = re.sub(single_quotes, u"'", self.source)
        # Convert colons
        self.source = self.source.replace(u'\uff1a', u':')
        # Remove line breaks and tabs
        self.source = self.source.replace(u'\n', u'')
        self.source = self.source.replace(u'\t', u'')
        # There are also some "zero width joiners" in random places
        # in the text.  Doesn't seem to cause any harm, though, so leave for now
        # these are the codeS: &#8205, &#160 (nbsp), \xa0 (nbsp), \u200d
        # Also previously had some non breaking spaces in unicode \u00a0, but this
        # may have been fixed by changing the parser below

        # Use the lxml cleaner
        cleaner = Cleaner()
        parser = HTMLParser(encoding='utf-8')
        # Finally, load the cleaned string to an ElementTree
        self.tree = cleaner.clean_html(lxml.html.fromstring(to_string(self.source), parser=parser))
        # self.tree = lxml.html.fromstring(to_string(self.source))

    def _clean(self):
        """
        Removes some of extraneous tags to make parsing easier
        """
        etree.strip_tags(self.tree, 'strong')
        for xx in self.tree.find_class('pydocx-tab'):
            xx.drop_tag()

    def _parse(self):
        """
        Parse the source document and populate this object's properties
        """
        # The A is for special question sections, such as the agenda on June 18, 2014
        pattern = ur'^[IVA]+\.'
        current_section = None
        # Iterate over the elements in self.tree.iter()
        # We only want paragraphs and table, since these appear to be the main top level elements
        # ie. we don't want to go fully down the tree
        # This is buggy for converted DOC files, since usually the tables have nested p elements
        for elem in self.tree.iter('table', 'p'):
            # When we encounter a header element, figure out what section it is a header for
            text = elem.text_content().strip()
            if text == u'':
                continue
            if text and re.search(pattern, text):
                section_name = self._identify_section(text)
                if section_name is not None:
                    logger.info(u'Identified header {} as {}'.format(text, section_name))
                    current_section = section_name
                else:
                    logger.warn(u"Could not identify section from header {}".format(text))
                    current_section = "other"
                # If this is the first time in this section, initailize the array for storing stuff
                if getattr(self, current_section) is None:
                    setattr(self, current_section, [])
                self._headers.append((current_section, elem))
            else:
                # Add all the elements we encounter to the list for the current section until
                # we encounter another header element, or the end of the document
                if current_section is not None:
                    arr = getattr(self, current_section)
                    arr.append(elem)

        # Once all of the sections are split up, parse each of them separately
        for section in CouncilAgenda.SECTION_MAP.keys():
            if getattr(self, section) is not None:
                getattr(self, "_parse_{}".format(section))()
        # We won't parse others, since we don't know what those are

    def _parse_tabled_papers(self):
        """
        Parse elements for tabled papers
        """
        if self.tabled_papers is None:
            return
        # Filter out paragraphs, which are actually children of the table elements
        self.tabled_papers = [xx for xx in self.tabled_papers if xx.tag == u'table']
        logger.info(u'Parsing tabled papers from {} elements'.format(len(self.tabled_papers)))
        parsed_papers = []
        for tbl in self.tabled_papers:
            # We only care about tables, specifically:
            # 1) a table with "Subsidiary Legislation / Instruments as its title, and
            # 2) a table with "Other Paper" as the title
            # One difficulty is that not every table is headered, such as
            # in the case of the June 5 2013 agenda, since there was only one paper
            first_row_text = tbl[0].text_content().strip()
            match_text = LEGISLATION_E if self.english else LEGISLATION_C
            match_text2 = OTHER_PAPERS_E if self.english else OTHER_PAPERS_C
            if match_text in first_row_text:
                # Subsidiary legislation
                # Papers occur in single rows
                logger.debug(u'Found subsidiary legislation table')
                parsed_papers.append(self._parse_tabled_legislation(tbl[1:]))
            elif match_text2 in first_row_text:
                # Other papers table
                # rows occur in pairs, with the first row being the title
                # and the second row being the presenter
                logger.debug(u'Found other papers table')
                parsed_papers.append(self._parse_other_papers(tbl[1:]))
            else:
                # No title, try to infer the table
                # For some Chinese agendas, it seems like the title is not included
                # or it could be in a different element
                # Check the second row to see if there are parenthesis
                # If there are, then it's likely Other papers
                # Or, can check the last column to see if there is a legislation number
                paper_number = ur'\d+/\d+'
                last_col = tbl[0][-1].text_content().strip()
                match = re.match(paper_number, last_col)
                if match:
                    logger.debug(u'Inferred subsidiary legislation table')
                    parsed_papers.append(self._parse_tabled_legislation(tbl))
                else:
                    logger.debug(u'Inferred other papers table')
                    parsed_papers.append(self._parse_other_papers(tbl))
        self.tabled_papers_p = list(itertools.chain.from_iterable(parsed_papers))

    def _parse_tabled_legislation(self, tbl):
        parsed_papers = []
        for item in tbl:
            # Sometimes there are blank rows
            if item.text_content().strip() == u'':
                continue
            try:
                parsed = TabledLegislation(item)
            except IndexError:
                logger.warning(u'Could not parsed tabled legislation for agenda {}'.format(self.uid))
            logger.debug(u'Found legislation {}'.format(parsed.title))
            parsed_papers.append(parsed)
        return parsed_papers

    def _parse_other_papers(self, tbl):
        parsed_papers = []
        grouped_tbl = grouper(tbl, 2)
        for item in grouped_tbl:
            # In this case, item is tuple of tr elements
            parsed = OtherTabledPaper(item)
            logger.debug(u'Found other paper {}'.format(parsed.title))
            parsed_papers.append(parsed)
        return parsed_papers

    def _parse_members_bills(self):
        pass

    def _parse_members_motions(self):
        pass

    def _parse_questions(self):
        """
        Parse question lxml elements into AgendaQuestions.

        When this is run, self.questions is a list of lxml elements.  This will
        scan through the list, and give groups of elements that constitute a question
        to the AgendaQuestion constructor.
        """
        if self.questions is None:
            return

        logger.info(u"Parsing questions from {} elements".format(len(self.questions)))
        parsed_questions = []
        pattern = QUESTION_PATTERN_E if self.english else QUESTION_PATTERN_C
        parts = []
        for q in self.questions:
            content = q.text_content().strip()
            # Discard empty elements
            if content == '':
                continue
            # Match for question starts
            match = re.match(pattern, content)
            if match is not None:
                # Found a match for a new question start
                # If we've accumulated parts for a prior question, clear those out
                if len(parts) > 0:
                    ag = AgendaQuestion(parts, english=self.english)
                    parsed_questions.append(ag)
                    parts = []
                logger.debug(u"Found question {}".format(content))
            # Continue to accumulate parts
            parts.append(q)

        # Make sure to parse the last question
        ag = AgendaQuestion(parts, english=self.english)
        parsed_questions.append(ag)
        self.questions = parsed_questions
        logger.info(u"Parsed {} questions".format(len(self.questions)))

    def _parse_bills(self):
        pass

    def _parse_motions(self):
        pass

    def _identify_section(self, header):
        """
        Try to identify what section the header delineates
        Returns None if it can't identify the section, otherwise it returns the property
        """
        # Need to keep order of the map because we need to check for members' bills before
        # we check for bills
        for prop_name, check_strings in CouncilAgenda.SECTION_MAP.items():
            if any_in(check_strings, header):
                return prop_name
        return None

    def get_headers(self):
        """
        Gets the headers from the document
        """
        text = self.tree.xpath('//text()')
        pattern = ur'^[IV]+\.'
        res = []
        for p in text:
            if re.search(pattern, p):
                res.append(p)
        return res


class AgendaQuestion(object):
    """
    Object for questions listed in the CouncilAgenda

    Instantiate with the list of lxml elements that comprise
    the question, and this object will parse out the sections
    """
    RESPONDER_PATTERN = ur':\s?(.+)$'
    QTYPE_ORAL = 1
    QTYPE_WRITTEN = 2

    def __init__(self, elements, english=True):
        self._elements = elements

        # Get the asker
        text = elements[0].text_content().strip()
        pattern = QUESTION_PATTERN_E if english else QUESTION_PATTERN_C
        match = re.match(pattern, text)
        if match is not None:
            self.number = match.group(1)
            self.asker = match.group(2)
            # Get question type
            # Can be oral or written.  Could also be urgent, but have not yet seen how these are
            # indicated
            if text.startswith('*'):
                self.type = self.QTYPE_WRITTEN
            else:
                self.type = self.QTYPE_ORAL
        else:
            logger.warn(u'Could not find asker of question in element: {}'.format(text))
            self.number = None
            self.asker = None
            self.type = None

        # Get the responder
        # If the question is the last question, then there may be a note
        # that begins with an asterisk that says which questions were
        # for written reply
        # As a heuristic, just search the last two elements, and keep track
        # of which is the last index of the body of the question

        # In other cases, if there is more than one public officer to reply, then
        # the list of public officers could be split across two elements.  See, for example,
        # the agenda from June 18, 2014, question 1
        ending_index = -2
        for e in elements[-2:]:
            text = e.text_content().strip()
            match = re.search(AgendaQuestion.RESPONDER_PATTERN, text)
            if match is not None:
                self.replier = match.group(1)
                break
            ending_index += 1
        else:
            logger.warn(u'Could not find responder of question in element: {}'.format(text))
            self.replier = None

        # Store the rest of the elements into the body as html
        self.body = ''.join([etree.tounicode(xx, method='html') for xx in elements[1:ending_index]])

    def __repr__(self):
        return u'<Question by {}>'.format(self.asker).encode('utf-8')


class TabledLegislation(object):
    """
    Object for tabled subsidiary legislation and instruments.  These will
    typically have a canonical title and a legislation number

    Instantiated with a tr element that is a row in the table of subsidiary legislation
    """
    def __init__(self, row, english=True):
        # Sometimes the first column is a number, other times the number is not present
        # So we start from the last column, since that should always be the paper number
        self.number = row[-1].text_content().strip()
        # Sometimes there is a blank column in between the paper number and the title
        title = row[-2].text_content().strip()
        if title == '':
            self.title = row[-3].text_content().strip()
        else:
            self.title = title

    def __repr__(self):
        return u'<TabledLegislation {}: {}>'.format(self.number, self.title).encode('utf-8')


class OtherTabledPaper(object):
    """
    Other tabled papers

    Instantiated with a tuple of two tr elements
    """
    def __init__(self, rows, english=True):
        # The HTML uses things like BRs and spans to split the text up, but these
        # are removed by text_content().  So we'll need to add a space for each element,
        # then convert duplicate spaces to a single space
        title_elems = rows[0].xpath('.//*')
        for e in title_elems:
            e.tail = ' ' + e.tail if e.tail else ' '
        title = rows[0].text_content().strip()
        self.title = ' '.join(title.split())
        if rows[1] is not None:
            self.presenter = rows[1].text_content().strip()
        else:
            self.presenter = None

    def __repr__(self):
        return u'<OtherTabledPaper {}>'.format(self.title).encode('utf-8')


class AgendaMotion(object):
    """
    Object for Members' Motions.
    """
    def __init__(self, elements):
        self.mover = None
        self.body = None
        self.amendments = None


class MotionAmendment(object):
    """
    Amendments to motions.
    """
    def __init__(self, parent, elements):
        self.motion = parent
        self.amender = None
        self.body = None


def any_in(arr, iterable):
    """
    Checks if any value in arr is in an iterable
    """
    for elem in arr:
        if elem in iterable:
            return True
    return False


def get_all_agendas():
    from raw import models, utils
    objs = models.RawCouncilAgenda.objects.order_by('-uid').all()
    return objs


"""

# Try to figure out all of the strings that we need to account for
import logging
from raw.docs.agenda import get_all_agendas, CouncilAgenda
from raw import utils
agendas = get_all_agendas()
headers = []
for ag in agendas:
    logging.info(ag)
    if ag[1] == "DOCX":
        src = utils.docx_to_html(ag[2])
    elif ag[1] == "DOC":
        src = utils.doc_to_html(ag[2])
    else:
        continue
    res = CouncilAgenda(ag[0].uid, src)
    headers.append(res.get_headers())
import itertools
import re
pattern = ur'^[IV]+\.\s?'
flat_headers = list(itertools.chain.from_iterable(headers))
flat_headers = [re.sub(pattern, u'', xx) for xx in flat_headers]
unique_headers = set(flat_headers)
unique_headers = sorted(list(unique_headers))
# there are some headers that get the roman numerals, but miss the title, possibly because of some
# wayward tag

Address by the Chief Executive
Addresses
Bill
Bills
Election of President
Member's Bill
Member's Motion
Members' Bills
Members' Motion
Members' Motions
Members' Motions on Subsidiary Legislation and Other Instruments
Motion
Motions
Question under Rule 24(4) of the Rules of Procedure
Questions
Questions for Written Replies
Questions under Rule 24(4) of the Rules of Procedure
Special Motions
Statements
Tabling of Paper
Tabling of Papers
Taking of Legislative Council Oath
The Chief Executive of the Hong Kong Special Administrative Region   presents the Policy Address
The Chief Executive of the Hong Kong Special Administrative Region  presents the Policy Address
The Chief Executive of the Hong Kong Special Administrative Region  presents the Policy Address to the Council
The Chief Executive's Question and Answer Session
 Motions
以書面答覆的質詢
作出立法會誓言
提交文件
根據《議事規則》第24(4)條提出的質詢
法案
發言
聲明
行政長官發言
行政長官答問會
議員就附屬法例及其他文書提出的議案
議員法案
議員議案
議案
質詢
選舉主席
香港特別行政區行政長官向本會發表施政報告
香港特別行政區行政長官發表施政報告


from raw.docs.agenda import get_all_agendas, CouncilAgenda
from raw.models import RawCouncilAgenda
from raw import utils
import random

agendas = get_all_agendas()
objs = []
sample = [xx.get_parser() for xx in random.sample(agendas[0:100], 10)]

tbls = []
for xx in sample:
    tbls.append([xxx for xxx in xx.tabled_papers if xxx.tag == 'table'])


for ag in agendas:
    objs.append(ag.get_parser())

with open('doc_e.html', 'wb') as foo:
    foo.write(a.source.encode('utf-8'))

a = RawCouncilAgenda.objects.get(id=2357).get_parser()

# clean the html
from lxml.html.clean import clean_html
clean_res = clean_html(res)

# Write to local disk to testing
res = utils.doc_to_html(doc_e[2])
with open('doc_e.html', 'wb') as foo:
    foo.write(res.encode('utf-8'))
res = utils.doc_to_html(doc_c[2])
with open('doc_c.html', 'wb') as foo:
    foo.write(res.encode('utf-8'))

res = pydocx.docx2html(docx_e[2])
with open('docx_e.html', 'wb') as foo:
    foo.write(res.encode('utf-8'))
res = pydocx.docx2html(docx_c[2])
with open('docx_c.html', 'wb') as foo:
    foo.write(res.encode('utf-8'))

import shutil
shutil.copyfile(doc_e[2], './doc_e.doc')
shutil.copyfile(doc_c[2], './doc_c.doc')
shutil.copyfile(docx_e[2], './docx_e.docx')
shutil.copyfile(docx_c[2], './docx_c.docx')

"""


