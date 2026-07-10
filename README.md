# vk_sender

*docker build . -t vk_sender*

**nginx**

> location /vk-alerts { \
>    proxy_pass http://vk_sender:8000/notify; \
>    proxy_set_header X-VK-Token "TOKEN_ALERTS"; \
>    proxy_set_header X-Chat-ID "22222"; \
>    proxy_request_buffering off; \
>    proxy_http_version 1.1; \
> }
> 

> location /vk-cam-alarm { \
>    proxy_method POST; \
>    proxy_pass http://vk_sender:8000/notify; \
>    proxy_set_header X-VK-Token "TOKEN_ALERTS"; \
>    proxy_set_header X-Chat-ID "1"; \
>    proxy_request_buffering off; \
>    proxy_http_version 1.1; \
>
>    proxy_set_body '{"message":"Alarm!", "attach":"http://cams_ip/cgi-bin/snapshot.cgi?channel=1"}'; \
>    proxy_set_header Content-Type "application/json"; \
>
>}

> curl -X POST -H "Content-Type: application/json" -d '{"message":"Сработала тревога на датчике движения!"}'
> http://vk_sender/vk-alerts

---
**Copyright © 2026 Вайбкодинг.**  
ИИ [Алиса](https://alice.yandex.ru)&[Gemini](https://gemini.google.com)
