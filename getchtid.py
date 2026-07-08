#Получить id чата сообщества
import vk_api

token = "vk1.token..." # << VK token
vk = vk_api.VkApi(token=token).get_api()

try:
    # Используем корректное значение filter='chats'
    conversations = vk.messages.getConversations(filter='chats')

    if conversations['count'] == 0:
        print("Чатов не найдено")
    else:
        for item in conversations['items']:
            conv = item['conversation']
            peer = conv['peer']
            if peer['type'] == 'chat':
                chat_id = peer['local_id']  # Локальный ID чата внутри сообщества
                title = conv['chat_settings']['title']
                print(f"Chat ID: {chat_id}, Title: {title}")
except vk_api.exceptions.VkApiError as e:
    print(f"Ошибка VK API: {e.code} - {e.error_msg}")
