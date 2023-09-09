import json
import os
from rich import print
from rich.panel import Panel
from rich.text import Text
from collections import defaultdict
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_public_key
import crypto
import sqlite3

"""
Основной файл. В нем реализованы все основные функции, которые используется для голосования.
"""


def execute_query(query, params=()):
    """
    Выполняет SQL-запрос и возвращает результат. Мы могли бы делать это также, как в db_generator.py, но это менее
    удобно и менее читаемо. В таком случае бы пришлось при каждом запросе писать 3 строки, вместо одной. Поэтому,
    для нашего удобства мы вынесли это в отдельную функцию.
    """
    with sqlite3.connect("voting_database.db") as conn:
        cursor = conn.cursor()  # Создаем курсор - объект, который позволяет нам взаимодействовать с базой данных
        cursor.execute(query, params)  # Выполняем запрос
        result = cursor.fetchall()  # Получаем результат
        conn.commit()  # Сохраняем изменения (если они были)
    return result  # Возвращаем результат


def clear_console():
    """
    Функция "для красоты". Очищает консоль. Для удобства написания вынесено в
    отдельную функцию. Ввиду того, что функция и так небольшая, можно было бы этого не делать, но так удобнее.
    """
    os.system("cls")


def error(message):
    """
    Ещё одна функция "для красоты". Выводит сообщение об ошибке в виде панели с красной рамкой и символом ошибки.
    Возвращает эту панель. Вынесено в отдельную функцию для удобства (чтобы не повторять один и тот же код несколько
    раз).
    """
    error_text = Text("⚠️ " + message, style="bold red")
    error_panel = Panel(error_text, title="Error", border_style="red")
    return error_panel


"""
Следующие функции сделаны для удобства, чтобы не писать каждый раз запрос к БД. Они просто возвращают результат.
По факту это геттеры для БД.
"""


def list_of_tally_centers():
    """Короткий способ получить список всех избирательных участков."""
    return execute_query("SELECT * FROM tally_centers")


def list_of_candidates():
    """Короткий способ получить список всех кандидатов."""
    return execute_query("SELECT * FROM candidates")


def get_voter(passport):
    """Короткий способ получить данные избирателя по его паспорту."""
    return execute_query("SELECT * FROM voters WHERE id=?", (passport,))


def get_tally_center(tally_center_id):
    """Короткий способ получить данные избирательного участка по его ID."""
    return execute_query("SELECT * FROM tally_centers WHERE id=?", (tally_center_id,))


def to_vote(passport, private_key_serialized, candidate_id, tally_center_id):
    """
    Наша основная функция, в которой происходит вся логика голосования. Возвращает код ошибки, если она произошла, или
    True, если всё прошло успешно. Принимает на себя паспорт, приватный ключ, ID кандидата и ID избирательного участка.
    """
    # Проверяем, что голосование ещё не закончилось - нет результатов
    results = execute_query("SELECT COUNT(*) FROM tally_results")[0][0]
    if results != 0:
        """
        Если в таблице tally_results есть хотя бы одна запись, значит голосование уже закончилось. В таком случае
        мы не можем допустить дальнейшего голосования и завершаем функцию, возвращая код ошибки -6 
        (коды выбраны случайно).
        """
        return -6
    # Проверяем паспорт в базе
    voter = get_voter(passport)[0]  # 0 - потому что возвращается список, а нам нужен только первый элемент (id)
    # Если такого паспорта нет, то возвращаем 0
    if not voter:
        return 0
    try:
        """
        Пытаемся загрузить приватный ключ. Если не получается - возвращаем -1. Это может произойти, если пользователь
        дал неверный приватный ключ и пытается с ним проголосовать. То есть он не взял чужой ключ или сделал что-то 
        похожее, а пытается вместо ключа использовать что-то совершенно другое.
        
        Здесь мы делаем "десериализацию" ключа - превращаем его из строки в объект, который можно использовать для
        расшифровки. Соответственно, если ключ неверный, то он не сможет быть десериализован и мы получим ошибку.
        """
        user_private_key = serialization.load_pem_private_key(
            private_key_serialized,
            password=None,
            backend=default_backend()
        )
    except:
        return -1

    # Загружаем мастер-фразу из файла master_phrase.txt
    """
    Как уже говорилось ранее, для реализации ZKP (проверки, что пользователь имеет право голосовать и не голосует
    повторно, при этом не раскрывая сам его выбор мы используем мастер-фразу. Мы шифруем её с помощью приватного ключа 
    и помещаем в БД при голосовании. Соответственно, если в БД есть мастер-фраза, значит пользователь уже голосовал
    
    Для проверки того, что пользователь может голосовать мы просто "прогоняем" мастер-фразу туда-обратно. Сначала шифруем
    публичным ключом, потом расшифровываем приватным. Если получаем ту же фразу на выходе - пользователь действительно
    имеет право голосовать.
    """
    with open("master_phrase.txt", "r") as master_phrase_file:
        master_phrase = master_phrase_file.read()
    # В базе данных ищем zkp. Если он есть - значит человек уже голосовал
    if voter[3]:
        zkp = voter[3]
        # Дешифруем zkp с помощью приватного ключа
        decrypted_zkp = crypto.decrypt_vote(zkp, user_private_key)
        """
        Если расшифровали верно - человек уже голосовал и пытается сделать это снова. Возвращаем ошибку -2.
        Если расшифровали неверно - была попытка подделки записи (кто-то внес zkp в БД напрямую),
        обнуляем zkp и разрешаем человеку голосовать.
        """

        if decrypted_zkp == master_phrase:  # ZKP был настоящим
            return -2  # Попытка повторного голосования
        else:
            execute_query("UPDATE voters SET zkp=? WHERE id=?", (None, passport))  # Обнуляем zkp - заменяем его на None
    """
    Если человек не голосовал - мы должны проверить с помощью ZKP, что он вообще может это делать. Для этого мы 
    попробуем зашифровать мастер-фразу с помощью публичного ключа пользователя (который хранится в БД), а затем 
    расшифровать её же с помощью приватного ключа. Если получится - пользователь имеет право голосовать, если нет -
    нет.
    """
    try:
        user_public_key = load_pem_public_key(voter[2].encode('utf-8'))  # Загружаем публичный ключ из БД
    except:
        return -3  # Если не получилось - возвращаем ошибку -3 - неверный публичный ключ пользователя в БД (взломали)
    zkp = crypto.encrypt_vote(master_phrase, user_public_key)  # Шифруем мастер-фразу публичным ключом
    if crypto.decrypt_vote(zkp, user_private_key) != master_phrase:
        return -4  # Если расшифровали неверно - возвращаем ошибку -4 - неверный приватный ключ пользователя

    """
    Если все проверки пройдены, то голосуем. Получаем public key избирательного участка (из БД) по id участка. 
    При этом мы декодируем этот id как utf-8 (это просто для того, чтобы мы смогли нормально прочитать его"""
    public_key = load_pem_public_key(
        execute_query("SELECT public_key FROM tally_centers WHERE id=?", (tally_center_id,))[0][0].encode('utf-8'))

    # Шифруем с помощью публичного ключа голос (id кандидата)
    encrypted_vote = crypto.encrypt_vote(candidate_id, public_key)
    if encrypted_vote is None:
        # Если при шифровании произошла ошибка (публичный ключ в БД неверный) - возвращаем ошибку -5
        return -5
    # Записываем голос в базу
    execute_query("INSERT INTO votes (encrypted_vote, tally_center_id) VALUES (?, ?)",
                  (encrypted_vote, tally_center_id))
    # Записываем zkp в базу - как подтверждение того, что человек уже голосовал и не может голосовать повторно
    execute_query("UPDATE voters SET zkp=? WHERE id=?", (zkp, passport))
    # Возвращаем True - все прошло хорошо
    return True


# Функция подсчета голосов
def tally_votes(tally_center_id, tally_center_private_key):
    """
    Функция подсчета голосов. Принимает id участка и приватный ключ участка. Эта функция обработает все голоса,
    которые были сделаны на этом участке и дешифрует их с помощью приватного ключа участка. Затем она подсчитает
    результаты и запишет их в таблицу tally_results в БД.
    """
    # Проверка, существует ли вообще такой участок - пытаем получить его по id
    tally_center = get_tally_center(tally_center_id)[0]
    if not tally_center:
        # Если не получилось - возвращаем ошибку 0 - нет такого участка
        return 0
    try:
        # Попытка загрузить приватный ключ из строки
        private_key = serialization.load_pem_private_key(
            tally_center_private_key,
            password=None,
            backend=default_backend()
        )
    except:
        # Если не получилось - возвращаем ошибку -1 - неверный приватный ключ
        return -1

    # Получение из БД зашифрованных голосов по id участка
    encrypted_votes = execute_query("SELECT encrypted_vote FROM votes WHERE tally_center_id=?", (tally_center_id,))

    # Дешифрование голосов и подсчет результатов
    # results - словарь, в котором ключ - id кандидата, а значение - количество голосов за него
    results = defaultdict(int)
    for encrypted_vote_data in encrypted_votes:
        # Проходим по всем зашифрованным голосам. При этом каждый голос будет называться encrypted_vote_data.
        # Дешифруем голос с помощью приватного ключа участка
        decrypted_vote = crypto.decrypt_vote(encrypted_vote_data[0], private_key)
        if decrypted_vote is None:
            # Если при дешифровании произошла ошибка - возвращаем ошибку -2 - не подходящий приватный ключ
            return -2
        """
        Если все хорошо - добавляем в словарь результатов голос за кандидата с id, который мы получили при
        дешифровании голоса (decrypted_vote) - это id кандидата 
        """
        results[decrypted_vote] += 1

    """
    Так как результаты представляют из себя сложную структуру - словарь, а не просто, например, число мы не можем
    напрямую записать результаты в БД. Сначала мы должны преобразовать их в что-то более понятное для SQLLite, например
    в формат JSON. Для этого мы используем библиотеку json.
    """
    results_str = json.dumps(results)
    """
    Так как мы будем хранить результаты в незашифрованном виде (id кандидата и количество голосов за него), то 
    злоумышленник может легко их взломать, просто написав свои результаты. Разумеется, мы этого не хотим, для этого 
    полученные результаты мы подписываем с помощью приватного ключа участка. Для этого мы используем функцию
    sign_results из файла crypto. Эта функция возвращает подпись в формате base64. Мы записываем результаты и подпись
    в БД. (см файл crypto.py)
    """
    execute_query("INSERT INTO tally_results (tally_center_id, result, signature) VALUES (?, ?, ?)",
                  (tally_center_id, results_str, crypto.sign_results(results_str, private_key)))
    """
    Немного объяснений про SQL. В скобках мы указываем как поля называются в таблице, а вопросительные знаки - это
    то, что мы передадим внутрь запроса. Таким образом, вместо первого вопросительного знака будет подставлено
    значение tally_center_id, вместо второго - results_str и вместо третьего - подпись (все то, что идет после запятой
    после самого запроса. Такой формат будет использоваться везде, где мы будем передавать данные в БД.
    """
    # Возвращаем True - все прошло хорошо
    return True


# Функция проверки результатов
def check_votes():
    """
    Эта функция сразу выполняет несколько ролей: для начала она возвращает нам в удобном виде результаты голосования
    по каждому участку. Также она проверяет подписи участков и возвращает результат проверки, а значит предотвращает
    изменения результатов голосования в БД. По факту эта функция - реализация 6 пункта из ТЗ.
    """
    final_results = []  # Список, в котором будут храниться результаты голосования по каждому участку

    tally_centers = execute_query("SELECT * FROM tally_centers")  # Получаем список всех участков

    for tally_center in tally_centers:
        """
        Проходим по каждому участку, записываем его id и имя в переменные center_id и center_name соответственно.
        """
        center_id = tally_center[0]
        center_name = tally_center[1]

        #  Получаем результаты голосования по участку из БД
        tally_results = \
            execute_query("SELECT * FROM tally_results WHERE tally_center_id=? ORDER BY tally_center_id ASC",
                          (center_id,))[
                0]
        if not tally_results:
            #  Если результатов нет - возвращаем 0. Это означает, что голосование еще не закончено (не все участки)
            return 0

        #  Получаем публичный ключ участка из БД
        public_key = load_pem_public_key(tally_center[2].encode('utf-8'))
        if not crypto.verify_signature(tally_results[2], tally_results[3], public_key):
            #  Если подпись не прошла проверку - возвращаем -1 - ошибка. Значит результаты были кем-то изменены
            return -1

        """
        Ранее мы записывали результаты в БД в формате JSON. Теперь мы их получаем и преобразуем обратно в словарь
        """
        results = json.loads(tally_results[2])
        #  Список, в котором будут храниться результаты голосования по каждому центру
        center_results = []
        for candidate_id, votes_count in results.items():
            # Проходим по словарю с результатами и записываем в список результаты по каждому кандидату.
            # Получаем имя кандидата по его id
            candidate_name = \
                execute_query("SELECT name FROM candidates WHERE id=? ORDER BY id ASC", (candidate_id,))[0][0]
            # Записываем в список результаты по кандидату в формате (id, имя, количество голосов)
            center_results.append((candidate_id, candidate_name, votes_count))
        # Записываем в итоговый список результаты по участку в формате (id, имя, результаты по кандидатам)
        final_results.append((center_id, center_name, center_results))
    # Возвращаем итоговый список
    return final_results


# Функция вывода таблицы голосов на экран (для симуляции)
def print_votes():
    # Получение результатов голосования и проверка их подлинности
    results = check_votes()
    # Если получили ошибку (а это всегда если тип результатов не список) - просто возвращаем эту самую ошибку
    if not isinstance(results, list):
        return results
    # Словарь для хранения общего количества голосов для каждого кандидата по всем участках
    total_votes = defaultdict(int)

    for center_id, center_name, center_results in results:
        """
        Проходимся по каждому центру из результатов и выводим результаты по каждому кандидату.
        """
        print("{}".format(center_name))  # Выводим название центра
        for candidate_id, candidate_name, votes in center_results:
            # Выводим результаты по каждому кандидату в формате "Имя: количество голосов"
            print("{}: {}".format(candidate_name, votes))
            # Добавляем количество голосов к общему количеству голосов по кандидату
            total_votes[candidate_name] += votes
        print("")  # Пустая строка для разделения результатов разных центров

    # Вывод общего количества голосов
    print("Total votes:")
    for candidate_name, votes in total_votes.items():
        # Выводим результаты по каждому кандидату в формате "Имя: количество голосов"
        print("{}: {}".format(candidate_name, votes))
