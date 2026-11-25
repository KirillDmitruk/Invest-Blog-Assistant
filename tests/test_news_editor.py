import pytest
from invest_blog.news_editor import validate,compose,generate,number_facts

SOURCE='Компания увеличила выручку на 10% до 4 млрд рублей. Решение по дивидендам пока не принято.'
def draft():return {'headline':'Выручка растёт, дивиденды пока под вопросом','paragraphs':['Компания увеличила выручку на 10% до 4 млрд рублей. Решения по дивидендам пока нет.']}
def test_source_numbers_and_link_preserved():
    data=validate(draft(),SOURCE)
    result=compose({'category':'russia','url':'https://example.org/news','source':'Публичный источник'},data)
    assert '10%' in result and result.endswith('https://example.org/news')
@pytest.mark.parametrize('text',['Выручка увеличилась на 15% и компания продолжает развиваться.','Выручка достигла 4 млн рублей и компания продолжает развиваться.','Покупайте акции компании, пока ещё не стало слишком поздно.','Подписывайтесь на https://example.org и получайте доход.'])
def test_inventions_promotion_and_scaled_numbers_rejected(text):
    data=draft();data['paragraphs']=[text]
    with pytest.raises(ValueError):validate(data,SOURCE)
def test_currency_and_sign_cannot_be_replaced():
    assert number_facts('$100')!=number_facts('€100')
    assert number_facts('-10%')!=number_facts('10%')
def test_prompt_treats_source_as_data_and_contains_no_secrets(monkeypatch):
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'message':{'content':__import__('json').dumps(draft())}}
    class Session:
        def post(self,url,**kwargs):
            assert url=='http://127.0.0.1:11434/api/chat'
            assert kwargs['json']['think'] is False
            assert 'недоверенные данные' in kwargs['json']['messages'][0]['content']
            return Response()
    assert generate({'title':'Новость компании о квартальной выручке','summary':SOURCE,'category':'russia'},provider='ollama',session=Session())==draft()
def test_source_link_does_not_accept_embedded_credentials():
    with pytest.raises(ValueError):compose({'category':'russia','url':'https://secret@host/path','source':'Example'},draft())

def test_numeric_scales_translate_without_changing_amounts():
    assert number_facts('$4 billion')==number_facts('4 млрд долларов')
    assert number_facts('4 млрд руб.')==number_facts('4 млрд рублей')
    assert number_facts('10 percent')==number_facts('10%')
    assert number_facts('$4 billion')!=number_facts('4 млн долларов')

def test_grouped_thousands_preserve_value():
    assert number_facts('58 000 рублей')==number_facts('58000 руб.')
