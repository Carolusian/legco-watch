# -*- coding: utf-8 -*-
"""
Processor for Council Questions
"""
import logging
from urlparse import urljoin
import re
from raw.models import RawCouncilQuestion, LANG_EN, LANG_CN, RawMember
from raw.names import MemberName
from raw.processors.base import BaseProcessor, file_wrapper
from django.utils.timezone import now


logger = logging.getLogger('legcowatch')


class QuestionProcessor(BaseProcessor):
    def process(self):
        logger.info("Processing file {}".format(self.items_file_path))
        counter = 0
        # keys are fields in the jsonlines item, values are the fields in the model object
        field_map = {
            'asker': 'raw_asker',
            'reply_link': 'reply_link',
            'number_and_type': 'number_and_type',
            'date': 'raw_date',
            'source_url': 'crawled_from',
            'subject': 'subject',
        }
        matcher_en = RawMember.get_matcher()
        matcher_cn = RawMember.get_matcher(False)
        for item in file_wrapper(self.items_file_path):
            try:
                counter += 1
                # For each question, fill in the raw values, then try to match against a RawMember instance

                # Generate a uid and get the object
                uid = self._generate_uid(item)
                obj, created = RawCouncilQuestion.objects.get_or_create(uid=uid)
                if created:
                    self._count_created += 1
                else:
                    self._count_updated += 1

                # Fill in the last parsed and last crawled values
                if self.job is not None:
                    obj.last_crawled = self.job.completed
                obj.last_parsed = now()

                # Fill in the items that can be copied directly
                for k, v in field_map.items():
                    val = item.get(k, None)
                    setattr(obj, v, val)

                if obj.reply_link is None:
                    obj.reply_link = u''

                # the subject_link is sometimes a relative path, so convert it to an absolute url
                subject_link = item.get('subject_link', u'')
                if subject_link != u'':
                    abs_url = urljoin(item['source_url'], subject_link)
                    obj.subject_link = abs_url

                # Convert the language from the string to the constants
                lang = LANG_CN if item['language'] == u'C' else LANG_EN
                obj.language = lang
                if lang == LANG_CN:
                    matcher = matcher_cn
                else:
                    matcher = matcher_en

                # Try to find the RawMember object that matches the asker
                # There will still be some askers not matched - we will use parser to fix them soon
                raw_name = item['asker']
                # Some postprocessing
                # Get rid of 'Hon', '議員' and ''
                raw_name = raw_name.replace(u'Hon',u'')
                raw_name = raw_name.replace(u'議員',u'')
                
                # Get rid of heading and tailing spaces
                if raw_name[0]==u' ':
                    raw_name = raw_name[1:]
                if raw_name[-1]==u' ':
                    raw_name = raw_name[:-1]
                
                # Try to match the name with RawMember
                name = MemberName(raw_name)
                match = matcher.match(name)
                if match is not None:
                    member = match[1]
                    obj.asker = member
                else:
                    pass
                    #logger.warn(u'Cannot match asker "{}" with members in database'.format(raw_name))
                    
                # Get the local path of reply content
                try:
                    obj.local_filename = item['files'][0]['path']
                except IndexError:
                    obj.local_filename = None
                    logger.warn(u'Could not get local path for question {} from date {}'.format(item['number_and_type'], item['date']))
                
                # Sometimes the reply link is not available yet,
                # and sometimes the meeting was cancelled or deferred
                # In these cases, forget about them.
                if obj.local_filename is not None:
                    obj.save()
                
            except (KeyError, RuntimeError) as e:
                self._count_error += 1
                logger.warn(u'Could not process question {} from date {}'.format(item['number_and_type'], item['date']))
                logger.warn(unicode(e))
                continue
        #After saving all items, use parser to fix missing askers
        no_asker_list = RawCouncilQuestion.fix_asker_by_parser()
        
        logger.info(u"{} items processed, {} created, {} updated, {} errors, {} questions without asker".format(counter, self._count_created, self._count_updated, self._count_error, len(no_asker_list)))
        #for debugging
        print(no_asker_list)
        
    def _generate_uid(self, item):
        """
        UIDs for questions are of the form 'question-09.10.2013-1-e' (question-<date>-<number>-<lang>)
        """
        #most common, e.g. 'UQ. 2 (Oral)'
        number_re = ur'Q\.\s?(?P<number>\d{1,2})\s?\(?(?P<qtype>\w+)\)?'
        # if there is only 1 urgent question, e.g. 'UQ(Oral)'
        # in rare case the 'U' was omitted
        number_re_nonumber = ur'UQ\(?(?P<qtype>\w+)\)?'
        # in rare cases the type was omitted, e.g. 'Q. 8', but actually we do not use type in uid
        # and we can try to retrieve type via its other language counterpart
        number_re_no_type = ur'Q\.\s\(?(?P<number>\d+)\)?' 
        
        no_number_flag=0
        no_type_flag=0

        match = re.search(number_re, item['number_and_type'], re.UNICODE)
        if match is None:
            match = re.search(number_re_nonumber, item['number_and_type'], re.UNICODE)
            if match is not None:
                no_number_flag = 1
            else:
                match = re.search(number_re_no_type, item['number_and_type'], re.UNICODE)
                if match is not None:
                    no_type_flag = 1
                else:
                    raise RuntimeError(u'Could not parse number and type of question from {}'.format(item['number_and_type']))
        
        #is_urgent = u'UQ' in item['number_and_type']
        matches = match.groupdict()
        
        if no_number_flag==1:
            number = 0
        else:
            number = matches['number']
        
        lang = item['language'].lower()
        
        #in some very rare cases the number is 0 but no 'U'
        is_urgent = (u'UQ' in item['number_and_type']) or number=='0' or number==0
        
        date = item['date']
        # date is in format 'd.m.yyyy'. We want 'yyyymmdd'
        date_str = date.split('.')
        if len(date_str) ==3:
            yr = date_str[-1]
            mn = date_str[1] if len(date_str[1])==2 else '0'+date_str[1]
            dd = date_str[0] if len(date_str[0])==2 else '0'+date_str[0]
            date = yr+mn+dd
        else:
            raise RuntimeError(u'Incorrect date format: {}'.format(date))
        
        if not is_urgent:
            return u'question-{}-{}-{}'.format(date, number, lang)
        else:
            return u'question-{}-u{}-{}'.format(date, number, lang)
