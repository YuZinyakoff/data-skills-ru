"""Lexical classification with mention guards and two supporting contexts."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import csv
from functools import lru_cache
import hashlib
from pathlib import Path
import re

import re2


FIELDS = ('title', 'requirements_qualifications', 'responsibilities')
CONDITIONS = {'none', 'data_tool', 'software_development'}
COMPONENTS = ('A', 'B', 'C', 'D', 'AD', 'E', 'F')
SENTENCE_END = re.compile(r'[|;\n!?]|\.(?=\s|$)')
NEGATIVE = re.compile(r'не\s+(?:требу[а-яё]*|нуж[а-яё]*|предусмотр[а-яё]*)')
SKILL = re.compile(r'знан[а-яё]*|владен[а-яё]*|навык[а-яё]*|умени[а-яё]*|опыт[а-яё]*|уме[а-яё]*|зна[ею][а-яё]*')
COMPANY = re.compile(r'(?:наша|нашей)\s+компани[а-яё]*|(?:мы|компани[а-яё]*)\s+(?:использу[а-яё]*|применя[а-яё]*|работа[а-яё]*|разрабатыва[а-яё]*)|(?:наш[а-яё]*|используем[а-яё]*)\s+(?:ст[еэ]к|технологи[а-яё]*)|у\s+нас\s+(?:использу[а-яё]*|работа[а-яё]*)')
FUTURE_TRAINING = re.compile(r'обучим|научим|готовы\s+обучить|возможност[а-яё]*\s+изуч[а-яё]*')
THIRD_PARTY = re.compile(r'(?:взаимодейств[а-яё]*|согласован[а-яё]*)\s+с\s+(?:отдел[а-яё]*|команд[а-яё]*|специалист[а-яё]*)')
TEACHING = re.compile(r'преподаван[а-яё]*|преподава[а-яё]*|обучени[а-яё]*\s+(?:студент|школьник|слушател)[а-яё]*')
OWN_TASK = re.compile(r'(?:администрирован|установк|настройк|конфигурирован|проектирован|сопровожден|программирован|разработк|применени)[а-яё]*|'+TEACHING.pattern)
DEGREE_HEAD = re.compile(r'образован[а-яё]*|диплом[а-яё]*')
FRESH_HEAD = re.compile(SKILL.pattern+'|'+OWN_TASK.pattern+r'|пользовател[а-яё]*')
EDUCATION_TASK_BOUNDARY = re.compile(r'(?P<education>образован[а-яё]*)(?:\s+(?:высш[а-яё]*|средн[а-яё]*|профессиональн[а-яё]*|техническ[а-яё]*|обязательно|желательно)){0,3}\s+(?P<task>(?:администрирован|установк|настройк|конфигурирован|проектирован|сопровожден)[а-яё]*)')
DEGREE_LEAD = re.compile(r'(?:образован[а-яё]*|диплом[а-яё]*)[^|;\n.!?]{0,100}(?:специальност[а-яё]*|направлен[а-яё]*\s+подготовк[а-яё]*)')
QUOTED = re.compile(r'«[^»\n]+»|"[^"\n]+"')
LIST_GAP = re.compile(r'[\s,/:()\-]*(?:(?:или|и|либо)[\s,/:()\-]*)?')
URL = re.compile(r'(?:https?://|www\.)[^\s<>]+|(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s<>]*', re.ASCII)
DATA_TOOL = re.compile(r'\b(?:sql|python|vba|bex|postgresql|postgres|mysql|mariadb|mongodb)\b|\bsap[\s-]+bw\b', re.ASCII)
DEVELOPER_ROLE = re.compile(r'программист[а-яё]*|разработчик[а-яё]*|\b(?:software[ -]+engineers?|developers?|programmers?)\b', re.ASCII)
PROGRAMMING_TASK = re.compile('програм{1,2}ирован[а-яё]*|програм{1,2}ирова[а-яё]*|написан[а-яё]*\\s+(?:программн[а-яё]*\\s+)?код[а-яё]*|(?:разработк|доработк|модификац)[а-яё]*\\s+(?:программн[а-яё]*\\s+обеспечен[а-яё]*|по(?:\\s*[,;:.|)]|$)|приложен[а-яё]*|веб[а-яё-]*\\s*сайт[а-яё]*)|разработк[а-яё]*\\s+постанов(?:к|ок)[а-яё]*\\s+задач[а-яё]*\\s+(?:на|для)\\s+программн[а-яё]*\\s+(?:и\\s+информационн[а-яё]*\\s+)?обеспечен[а-яё]*',re.ASCII)
DATABASE_MENTION = re.compile(r'баз[а-яё]*\s+данн[а-яё]*|(?:^|[^а-яёa-z])бд(?:$|[^а-яёa-z])|\bdatabases?\b', re.ASCII)
DATABASE_USER_LEVEL = re.compile(r'^\s+на\s+уровне\s+(?:(?:квалифицированн[а-яё]*|квалиф\.?|опытн[а-яё]*|уверенн[а-яё]*)\s+)?пользовател[а-яё]*')
DATABASE_RECORD_KEEPING = re.compile(r'(?:веден|заполнен|внесен|ввод)[а-яё]*\s+(?:[а-яё]+\s+){0,4}$')



B_MODEL_NOUN = r'математическ[а-яё]*\s+модел(?:ь|и|ей|ям|ями|ях|ью|ю|е)(?![а-яё])'
B_PROGRAM_INPUT = re.compile('(?:(?:на\\s+|а\\s+)?основе\\s+(?:анализ[а-яё]*\\s+|заданн[а-яё]*\\s+)?|описан[а-яё]*\\s+задач[а-яё]*\\s+(?:с\\s+помощью|посредством)\\s+)(?P<models>математическ[а-яё]*\\s+модел(?:ь|и|ей|ям|ями|ях|ью|ю|е)(?![а-яё]))')
B_PROGRAM_WORK = re.compile('(?:разработк|разрабатыва|составлен|составля|запис|написан|настройк|настраива|сопровожден)[а-яё]*[^|;\\n.!?]{0,200}?\\bпрограмм(?:у|ы|а|е|ой|ам|ами|ах|н[а-яё]*)?\\b|программн[а-яё]*\\s+код[а-яё]*|язык[а-яё]*\\s+программирован[а-яё]*')
B_GEOMETRIC_REFERENCE = re.compile(
    r'на\s+соответстви[а-яё]*\s+(?:чертеж[а-яё]*\s+или\s+)?'
    r'(?P<models>'+B_MODEL_NOUN+r')')
B_PHYSICAL_MEASUREMENT = re.compile(r'измерен[а-яё]*|геометрическ[а-яё]*|детал[а-яё]*|чертеж[а-яё]*')


def quantitative_template_reason(text,start,end,software_context):
    boundary_text = re.sub(r'\bдр\.(?=\s)','др ',text)
    for match in B_PROGRAM_INPUT.finditer(text):
        left,right = match.span('models')
        if left<=start and end<=right:
            fragment,_,_ = surrounding(boundary_text,match.start(),match.end())
            if software_context or B_PROGRAM_WORK.search(fragment):
                return 'programmer_template_without_independent_quantitative_demand'
    for match in B_GEOMETRIC_REFERENCE.finditer(text):
        left,right = match.span('models')
        if left<=start and end<=right:
            fragment,_,_ = surrounding(boundary_text,match.start(),match.end())
            if B_PHYSICAL_MEASUREMENT.search(fragment):
                return 'physical_comparison_to_model_not_quantitative_modeling'
    for match in re.finditer(B_MODEL_NOUN,text):
        if match.start()<=start and end<=match.end() and B_CAD_SUFFIX.match(text,match.end()):
            return 'explicit_geometric_2d_3d_model_not_quantitative_method'
    return ''



def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@lru_cache(maxsize=256)
def sentence_limits(text):
    boundaries = list(SENTENCE_END.finditer(text))
    return tuple(m.start() for m in boundaries), tuple(m.end() for m in boundaries)


def surrounding(text, start, end):
    starts, ends = sentence_limits(text)
    left_index, right_index = bisect_right(ends, start)-1, bisect_left(starts, end)
    left, right = (ends[left_index] if left_index >= 0 else 0), (starts[right_index] if right_index < len(starts) else len(text))
    return text[left:right], start-left, end-left


def mask_degree_titles(text):
    spans, cursor = [], None
    for match in QUOTED.finditer(text):
        leads = list(DEGREE_LEAD.finditer(text[:match.start()]))
        lead = leads[-1] if leads else None
        new_list = lead is not None and LIST_GAP.fullmatch(text[lead.end():match.start()])
        continued = cursor is not None and LIST_GAP.fullmatch(text[cursor:match.start()])
        if new_list or continued:
            spans.append((match.start(), match.end()))
            cursor = match.end()
        else:
            cursor = None
    chars = list(text)
    for start, end in spans:
        chars[start:end] = ' '*(end-start)
    return ''.join(chars)


def education_adjustment(text, start, end):
    sentence, relative_start, _ = surrounding(text, start, end)
    left = sentence.rfind(',', 0, relative_start)+1
    boundaries = [m for m in EDUCATION_TASK_BOUNDARY.finditer(sentence, left) if m.start('task') <= end-(start-relative_start)]
    if not boundaries:
        return text
    match, offset = boundaries[-1], start-relative_start
    a, b = offset+match.start('education'), offset+match.end('education')
    return text[:a]+' '*(b-a)+text[b:]


def _basic_reason(text, start, end, field):
    sentence, a, b = surrounding(text, start, end)
    left, right = sentence.rfind(',', 0, a)+1, sentence.find(',', b)
    right = len(sentence) if right < 0 else right
    clause, a, b = sentence[left:right], a-left, b-left
    optional = re.match(r',?\s*но\s+(?:будет\s+)?(?:плюс[а-яё]*|преимуществ[а-яё]*)', sentence[right:])
    for negative in NEGATIVE.finditer(clause):
        negates = False
        if negative.start() >= b:
            negates = not re.search(r'знан[а-яё]*|опыт[а-яё]*|навык[а-яё]*|наличи[а-яё]*|предоставлен[а-яё]*', clause[b:negative.start()])
        elif negative.end() <= a:
            between = clause[negative.end():a].strip()
            negates = not between or bool(re.match(r'(?:знан[а-яё]*|опыт[а-яё]*|навык[а-яё]*|владен[а-яё]*|умени[а-яё]*)\b', between))
        if negates and not optional:
            return 'explicit_negative_for_this_mention'
    if re.search(r'без\s+(?:знан[а-яё]*|навык[а-яё]*|владен[а-яё]*)', clause):
        return 'without_this_competence'
    if COMPANY.search(sentence) and not SKILL.search(sentence):
        return 'company_stack_without_candidate_requirement'
    if FUTURE_TRAINING.search(sentence) and not SKILL.search(sentence):
        return 'future_training_without_current_competence'
    if field == 'requirements_qualifications':
        heads = list(re.finditer(r'образован[а-яё]*|диплом[а-яё]*|специальност[а-яё]*', clause[:a]))
        if heads and not SKILL.search(clause[heads[-1].end():a]):
            return 'education_description_without_separate_competence'
    return ''


def _base_with_education(text, start, end, field):
    reason = _basic_reason(text, start, end, field)
    if reason == 'education_description_without_separate_competence':
        adjusted = education_adjustment(text, start, end)
        reason = _basic_reason(adjusted, start, end, field)
    return reason


def qualification_head(sentence, head, field):
    if head.group().startswith('диплом'):
        return True
    before, after = sentence[:head.start()], sentence[head.end():]
    if re.match(r'\s+(?:обучающ|воспитанник|ученик|студент|школьник|слушател)[а-яё]*', after):
        return False
    level = re.search(r'(?:высш|средн|профессиональн|техническ|специальн)[а-яё]*(?:[ /-]+[а-яё]+){0,3}\s*$', before)
    direction = (re.match(r'\s*(?::|-|по\s|в\s+(?:области|сфере)|высш|средн|не\s+ниже)', after)
                 if head.group() == 'образование' else re.match(r'\s+по\s+(?:специальност|направлен)', after))
    return bool(level or direction or (head.group() == 'образование' and (field == 'requirements_qualifications' or not before.strip())))


REVERSE_DEGREE = re.compile('\\((?P<subject>[^()\\n]{1,160})\\)\\s+(?:(?:высш|средн|профессиональн|техническ)[а-яё]*\\s+){0,3}образован[а-яё]*')

def reverse_degree_reason(text, start, end, field):
    if field != 'requirements_qualifications':
        return ''
    for match in REVERSE_DEGREE.finditer(text):
        left, right = match.span('subject')
        if left <= start and end <= right and not SKILL.search(match.group('subject')):
            return 'parenthesized_degree_subject_without_separate_competence'
    return ''


def _mention_reason(text, start, end, field):
    if reason := reverse_degree_reason(text, start, end, field):
        return reason
    for match in URL.finditer(text):
        value = match.group()
        actual_url = value.startswith(('http://', 'https://', 'www.')) or re.search(r'[?=&]|\.(?:php|html?|aspx?|jsp|cgi)(?:[/?#]|$)', value)
        if actual_url and start < match.end() and end > match.start():
            return 'mention_within_url'
    sentence, a, b = surrounding(text, start, end)
    left, right = sentence.rfind(',', 0, a)+1, sentence.find(',', b)
    right = len(sentence) if right < 0 else right
    clause, within_start, within_end = sentence[left:right], a-left, b-left
    reason = _base_with_education(text, start, end, field)
    if reason == 'company_stack_without_candidate_requirement' and not COMPANY.search(clause) and FRESH_HEAD.search(clause[:within_end]):
        offset = start-a
        adjusted = text[:offset]+' '*left+clause+' '*(len(sentence)-right)+text[offset+len(sentence):]
        reason = _base_with_education(adjusted, start, end, field)
    if not reason:
        for negative in NEGATIVE.finditer(sentence, b):
            optional = re.match(r'\s*,?\s*но\s+(?:будет\s+)?(?:плюс[а-яё]*|преимуществ[а-яё]*)', sentence[negative.end():])
            if not FRESH_HEAD.search(sentence[b:negative.start()]) and not optional:
                reason = 'explicit_negative_for_this_mention'
                break
        relation = THIRD_PARTY.search(clause[:within_end])
        if not reason and relation and not SKILL.search(clause[relation.end():within_start]):
            reason = 'third_party_not_own_competence'
        if not reason and COMPANY.search(clause) and not FRESH_HEAD.search(clause[:within_start]):
            reason = 'company_stack_without_candidate_requirement'
        heads = list(DEGREE_HEAD.finditer(sentence[:a]))
        if not reason and heads and not FRESH_HEAD.search(sentence[heads[-1].end():a]) and education_adjustment(text, start, end) == text:
            reason = 'degree_without_separate_competence'
    education_reasons = {'degree_without_separate_competence', 'education_description_without_separate_competence'}
    if reason and reason not in education_reasons:
        return reason
    heads = list(DEGREE_HEAD.finditer(sentence[:a]))
    if not heads or not qualification_head(sentence, heads[-1], field):
        return '' if reason == 'degree_without_separate_competence' else reason
    visible = list(sentence)
    for quote in re.finditer(r'«[^»]*»|"[^\"]*"|“[^”]*”', sentence):
        visible[quote.start():quote.end()] = ' '*(quote.end()-quote.start())
    visible = ''.join(visible)
    tail = visible[heads[-1].end():a]
    if re.search(SKILL.pattern+r'|пользовател[а-яё]*', tail):
        return ''
    if re.match(r'\s*,', tail):
        clause_start = visible.rfind(',', heads[-1].end(), a)+1
        own_clause = visible[clause_start:b].lstrip()
        if OWN_TASK.match(own_clause) and len(re.findall(r'[a-zа-яё]+', own_clause)) >= 2:
            return ''
    separators = list(re.finditer(r'[.!?;|\n]', tail))
    if separators and FRESH_HEAD.match(visible[heads[-1].end()+separators[-1].end():b].lstrip()):
        return ''
    return reason or 'degree_without_separate_competence'


B_CAD_SUFFIX = re.compile('\\s*\\(\\s*[23][ -]?d\\s*\\)')

EXTERNAL_ROLE = re.compile('(?:взаимодейств[а-яё]*|согласован[а-яё]*|сотрудничеств[а-яё]*)\\s+с\\s+(?:(?:внешн|сторонн|друг)[а-яё]*\\s+)?(?:разработчик|программист|администратор|подразделени|служб)[а-яё]*')

REQUEST_FOR_WORK = re.compile('(?:сбор|при[её]м|согласован|оформлен|передач)[а-яё]*\\s+(?:[а-яё]+\\s+){0,2}заяв(?:к|ок)[а-яё]*\\s+на\\s+')

TRAINEE_ROLE = re.compile('ученик[а-яё]*|стаж[её]р[а-яё]*|помощник[а-яё]*')

TRAINING_NOUN = re.compile('обучени[а-яё]*')

def _scope_reason(text,start,end,field,candidate_title=''):
    if reason := _mention_reason(text,start,end,field):
        return reason
    sentence,a,b = surrounding(text,start,end)
    left = sentence.rfind(',',0,a)+1
    clause,prefix = sentence[left:],sentence[left:a]
    for relation in EXTERNAL_ROLE.finditer(prefix):
        if not FRESH_HEAD.search(prefix[relation.end():]):
            return 'third_party_not_own_competence'
    for request in REQUEST_FOR_WORK.finditer(prefix):
        if not FRESH_HEAD.search(prefix[request.end():]):
            return 'request_for_other_work_not_own_competence'
    if TRAINEE_ROLE.search(candidate_title):
        training = list(TRAINING_NOUN.finditer(prefix))
        if training and not FRESH_HEAD.search(prefix[training[-1].end():]):
            return 'trainee_future_training_without_current_competence'
    return ''


DEVELOPER_NONTECH = re.compile('\\s+(?:мебел|одежд|обув|рецептур)[а-яё]*')
NONTECH_SUBJECT = re.compile('мебел[а-яё]*|одежд[а-яё]*|обув[а-яё]*|рецептур[а-яё]*')
SOFTWARE_TITLE = re.compile('программ|\\b(?:software|web|it|data)\\b|веб|приложен|данн|информационн[а-яё]*\\s+систем')
MATH_PROGRAMMING = re.compile('(?:динамическ|линейн|целочисленн)[а-яё]*\\s+$')
CAFE_CONTEXT = re.compile('кафе|кофейн|заведени|бариста')
TECHNICAL_CONTEXT = re.compile('фреймворк|framework|нейросет|нейрон|алгоритм|искусственн|машинн[а-яё]*\\s+обуч|программн|\\b(?:python|caffe|opencv|tensorflow)\\b')
MANUAL_VISION = re.compile('вручную|ручн[а-яё]*\\s+(?:просмотр|распознаван)|визуальн[а-яё]*\\s+досмотр|без\\s+(?:применени[а-яё]*\\s+)?алгоритм')
SAS_SOFTWARE = re.compile('статист|аналит|отч[её]т|программ(?:ир|н)|пакет|платформ|язык|консультир|эксперт|\\b(?:sql|spss|python|rstudio|splunk|enterprise|forecast|visual|base)\\b')
SAS_OTHER = re.compile('sas\\s*[.-]?\\s*(?:планет|planet|70\\b)|\\bserial[ -]+attached\\b|(?:интерфейс|контроллер|накопител|диск)[а-яё]*[^|;.!?]{0,50}\\bsas\\b|\\bsas\\b[^|;.!?]{0,30}(?:интерфейс|контроллер|накопител|диск)[а-яё]*')
COMMERCIAL_SAS = re.compile('продаж|продвиг|продвиж|закуп|поставк|лицензи')
SAS_OWN_SKILL = re.compile('программир|аналит|статист|консультир|эксперт')

def general_reason(text, start, end, field, candidate_title=''):
    if reason := _scope_reason(text, start, end, field, candidate_title):
        return reason
    sentence, a, b = surrounding(text, start, end)
    quote = text[start:end].strip()
    if re.fullmatch(r"разработчик[а-яё]*|developers?", quote):
        if DEVELOPER_NONTECH.match(text, end) or (field=='title' and NONTECH_SUBJECT.search(candidate_title) and not SOFTWARE_TITLE.search(candidate_title)):
            return 'explicit_nonsoftware_developer_subject'
    if re.fullmatch(r"програм{1,2}ирован[а-яё]*", quote) and MATH_PROGRAMMING.search(text[max(0, start-45):start]):
        return 'mathematical_programming_not_software_task'
    if quote == 'elt' and (re.search(r"english\s+language\s+teaching", sentence) or
                           re.search(r"(?:английск|english)", candidate_title) and not re.search(r"данн|\b(?:sql|etl|data|warehouse)\b", sentence)):
        return 'english_language_teaching_not_data_ELT'
    if quote == 'caffe' and CAFE_CONTEXT.search(sentence + ' ' + candidate_title):
        # Exclude the product name itself from the independent technical check.
        context = sentence[:a] + sentence[b:]
        if not TECHNICAL_CONTEXT.search(context):
            return 'cafe_name_not_ML_framework'
    if re.fullmatch(r"информационн[а-яё]*\s+безопасност[а-яё]*", quote) and re.search(r"соблюд[а-яё]*[^|;.!?]{0,60}(?:правил|требован|норм)", sentence[:a]):
        return 'routine_security_compliance_not_special_IT_competence'
    technical_sentence = re.sub(r"без\s+(?:применени[а-яё]*\s+)?алгоритм[а-яё]*", '', sentence)
    if (re.search(r"видео[ -]*аналитик|распознаван[а-яё]*\s+объект", quote) and
            MANUAL_VISION.search(sentence) and not TECHNICAL_CONTEXT.search(technical_sentence)):
        return 'explicit_manual_inspection_not_algorithmic_vision'
    for token in re.finditer(r"(?<![a-zа-яё0-9])r(?![a-zа-яё0-9])", text[start:end]):
        if re.match(r"r[\s.\-\u2010-\u2015]*keeper\b", text[start+token.start():]):
            return 'R_keeper_not_statistical_R'
    if quote == 'sas':
        local = sentence[max(0, a-180):min(len(sentence), b+180)]
        if text[max(0, start-1):start] == '@' or text[end:end+1] == '@':
            return 'SAS_contact_not_software'
        if SAS_OTHER.search(local):
            return 'SAS_other_explicit_meaning'
        if COMMERCIAL_SAS.search(local) and not SAS_OWN_SKILL.search(local):
            return 'SAS_commercial_reference_without_special_skill'
        if not SAS_SOFTWARE.search(local):
            return 'SAS_without_software_context'
    return ''


def load_lexicon(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle, delimiter=';'))
    required = {'term_id', 'component', 'pattern', 'fields', 'requires', 'provenance'}
    if not rows or not required <= set(rows[0]) or len({r['term_id'] for r in rows}) != len(rows):
        raise ValueError('Invalid lexicon schema or duplicate ID')
    for row in rows:
        if row['component'] not in 'ABCDEF' or len(row['component']) != 1 or row['requires'] not in CONDITIONS:
            raise ValueError('Invalid component/condition')
        if not row['fields'] or not set(row['fields'].split('|')) <= set(FIELDS):
            raise ValueError('Invalid field restriction')
    return rows


def normalize_records(records):
    result = {}
    for row in records:
        key = row.get('identifier')
        if not isinstance(key, str) or not key.strip() or key in result:
            raise ValueError('Record identifiers must be unique nonempty strings')
        values = {f:row.get(f, row.get('annotation_title', '') if f == 'title' else '') for f in FIELDS}
        if any(not isinstance(v, str) for v in values.values()):
            raise ValueError('Text fields must be strings')
        result[key] = {f:value.lower() for f,value in values.items()}
    return result


def conditional_reason(item, text, contexts):
    condition = item['requires']
    if condition == 'none':
        return ''
    if not contexts[condition]:
        return 'missing_'+condition
    # A developer elsewhere in the vacancy does not turn clerical DB use into engineering.
    if condition == 'software_development' and DATABASE_MENTION.search(item['matched_text']):
        if DATABASE_USER_LEVEL.search(text[item['end']:item['end']+80]):
            return 'database_user_level_not_engineering'
        sentence, start, _ = surrounding(text, item['start'], item['end'])
        if DATABASE_RECORD_KEEPING.search(sentence[:start]):
            return 'database_record_keeping_not_engineering'
    return ''


@lru_cache(maxsize=256)
def lexical_pattern(pattern):
    options = re2.Options()
    options.log_errors = False
    try:
        compiled = re2.compile('(?s:.*?)((?:' + pattern + '))', options=options)
    except re2.error as exc:
        raise ValueError(f'Invalid RE2 pattern: {pattern}') from exc
    if compiled.search('') is not None:
        raise ValueError('Empty lexical matches are forbidden')
    return compiled


def classify(records, lexicon, collect_evidence=True):
    texts = normalize_records(records)
    if not texts:
        raise ValueError('No records')
    flags = {key: dict.fromkeys(COMPONENTS, False) for key in texts}
    contexts = {key: {'data_tool': False, 'software_development': False} for key in texts}
    support, occurrences = ([], [])
    values = [(key, f, t, mask_degree_titles(t) if f == 'requirements_qualifications' else t) for key, row in texts.items() for f, t in row.items()]
    for row in lexicon:
        pattern = lexical_pattern(row['pattern'])
        fields = row['fields'].split('|')
        for key, field, original, masked in values:
            if field not in fields:
                continue
            for match in pattern.finditer(masked):
                start, end = match.span(1)
                quote = match.group(1)
                if not quote:
                    raise ValueError('Empty lexical matches are forbidden')
                reason = general_reason(masked, start, end, field, candidate_title=texts[key]['title'])
                occurrences.append(dict(identifier=key, term_id=row['term_id'], component=row['component'], field=field, start=start, end=end, quote=original[start:end], matched_text=quote, requires=row['requires'], local_reason=reason))
    for key, fields in texts.items():
        for field, text in fields.items():
            masked = mask_degree_titles(text) if field == 'requirements_qualifications' else text
            patterns = [('data_tool', DATA_TOOL), ('software_development', PROGRAMMING_TASK)]
            if field == 'title':
                patterns.append(('software_development', DEVELOPER_ROLE))
            for context, pattern in patterns:
                for match in pattern.finditer(masked):
                    if not general_reason(masked, match.start(), match.end(), field, candidate_title=texts[key]['title']):
                        contexts[key][context] = True
                        support.append(dict(identifier=key, context=context, field=field, start=match.start(), end=match.end(), quote=text[match.start():match.end()]))
    for item in occurrences:
        if not item['local_reason'] and item['component'] == 'B':
            item['local_reason'] = quantitative_template_reason(texts[item['identifier']][item['field']], item['start'], item['end'], contexts[item['identifier']]['software_development'])
    for item in occurrences:
        if not item['local_reason'] and item['requires'] == 'none' and (item['component'] in ('A', 'B')):
            contexts[item['identifier']]['data_tool'] = True
            support.append(dict(identifier=item['identifier'], context='data_tool', field=item['field'], start=item['start'], end=item['end'], quote=item['quote']))
    evidence = []
    for item in occurrences:
        reason = item['local_reason'] or conditional_reason(item, texts[item['identifier']][item['field']], contexts[item['identifier']])
        if not reason:
            flags[item['identifier']][item['component']] = True
        if collect_evidence:
            evidence.append(dict(item, accepted=not bool(reason), reason=reason or 'accepted'))
    for row in flags.values():
        row['AD'] = any((row[c] for c in 'ABCD'))
    return (flags, evidence, support if collect_evidence else [])
