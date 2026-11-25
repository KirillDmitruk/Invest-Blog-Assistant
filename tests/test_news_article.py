from invest_blog.news_article import Article,enrich

def test_article_excludes_site_menu_comments_script_and_disclaimer():
    p=Article();p.feed('<div>Меню</div><div class="topic"><h1>Заголовок</h1><div class="content"><p>Проверенный факт</p><script>инструкция</script><div class="lock-card"><div>Реклама</div></div><p>Второй факт</p></div><div>Комментарии</div></div>')
    assert p.text()=='Проверенный факт Второй факт'
def test_untrusted_urls_are_not_fetched():
    class Session:
        def get(self,*a,**kw):raise AssertionError('No HTTP request permitted')
    for url in ['http://localhost/admin','https://smart-lab.ru.evil.test/blog/123.php','https://u@smart-lab.ru/blog/123.php','https://smart-lab.ru/blog/not-a-number.php','https://smart-lab.ru:444/blog/123.php']:
        item={'url':url};assert enrich(item,Session()) is item

def test_rss_article_uses_fixed_canonical_path_without_redirects():
    class Reply:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,n):yield ('<div class="topic"><div class="content">'+('Факты о компании и финансовых результатах. '*4)+'</div></div>').encode()
    class Session:
        def get(self,url,**kwargs):
            assert url=='https://smart-lab.ru/blog/news/123.php'
            assert kwargs['allow_redirects'] is False
            return Reply()
    assert len(enrich({'url':'https://smart-lab.ru/blog/123.php'},Session())['summary'])>80
