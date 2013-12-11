import logging

from math import ceil

from django.db import connection
from django.core.paginator import Page

from django.core.paginator import EmptyPage, PageNotAnInteger

import sqlparse
from sqlparse import sql
from sqlparse import tokens

logger = logging.getLogger(__name__)


class SqlPaginator(object):

    def __init__(self, initial_sql, model, order_by='id', page=1,
                count=None, per_page=10, direction='asc', params=None):

        # if these are not an int then fail
        self.page_num = int(page)
        self.per_page = int(per_page)
        try:
            self._count = int(count)
        except TypeError:
            self._count = None

        # need the model class to do some sql validation
        self.model = model
        self.params = params or []

        self._num_pages = None
        self.orphans = 0
        self._initial_raw_sql = initial_sql
        self.initial_sql = initial_sql
        self.object_list = []

        self.allow_empty_first_page = True

        # resolve the model fields into their names
        self._model_fields = [f.name for f in model._meta.fields]

        self._db_table = self.model._meta.db_table

        self.order_by = order_by

        self.direction = direction
        if direction.lower() not in ['asc', 'desc']:
            self.direction = 'asc'

        # order_by queries work differently when using select distinct queries
        # maybe i should use a sql parser ?
        self._tsql = '%(sql)s order by %(order_by)s %(direction)s limit %(limit)d offset %(offset)d'

        # get the token list from the query, there will be only one
        tlist = sqlparse.parse(initial_sql)[0]
        from_token = tlist.token_next_match(0, tokens.Keyword, "FROM")

        select_token = tlist.token_next_match(0, tokens.DML, "Select")

        found = False
        for t in tlist.tokens_between(select_token, from_token, exclude_end=True):
            if t.value.lower().find(order_by.lower()) > -1:
                found = True
                break

        if not found:
            tlist.insert_before(from_token, sql.Token(tokens.DML, ",%s " % order_by))

        self.initial_sql = tlist.to_unicode()

        # dict to resolve the sql template with
        self.d = {'sql': self.initial_sql,
                  'order_by': order_by,
                  'offset': int(page - 1) * self.per_page,
                  'limit': self.per_page,
                  'direction': direction,
                 }

        self._sql = self._tsql % self.d

    def get_sql(self):
        return self._sql
    sql = property(get_sql)

    def _get_count(self):
        if self._count is None:
            cursor = connection.cursor()
            sql = "select count(distinct(%s)) from (%s) as q" % (self.model._meta.pk.name, self._initial_raw_sql)
            cursor.execute(sql)
            rows = cursor.fetchall()
            count = int(rows[0][0])
            self._count = count
        return self._count
    count = property(_get_count)

    def _get_num_pages(self):
        if self._num_pages is None:
            if self.count == 0:
                self._num_pages = 0
            else:
                hits = max(1, self.count, self.orphans)
                self._num_pages = int(ceil(hits / float(self.per_page)))

        return self._num_pages
    num_pages = property(_get_num_pages)

    def validate_number(self, number):
        "Validates the given 1-based page number."
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise PageNotAnInteger('That page number is not an integer')
        if number < 1:
            raise EmptyPage('That page number is less than 1')
        if number > self.num_pages:
            if number == 1 and self.allow_empty_first_page:
                pass
            else:
                raise EmptyPage('That page contains no results')
        return number

    def page(self, number, order_by=None, direction=None):
        number = self.validate_number(number)

        if not order_by and order_by not in self._model_fields:
            order_by = self.order_by

        if not direction or direction.lower() not in ['asc', 'desc']:
            direction = self.direction

        self.d.update({'offset': (number - 1) * self.per_page,
                       'order_by': order_by,
                       'direction': direction,
                       })

        logger.debug('count: %d' % self.count)
        logger.debug('num_pages: %d' % self.num_pages)

        sql = self._tsql % self.d
        logger.debug("sql: %s" % sql)

        self.object_list = list(self.model.objects.raw(sql, self.params))

        return Page(self.object_list, number, self)

    def _get_page_range(self):
        """
        Returns a 1-based range of pages for iterating through within
        a template for loop.
        """
        return range(1, self.num_pages + 1)
    page_range = property(_get_page_range)
