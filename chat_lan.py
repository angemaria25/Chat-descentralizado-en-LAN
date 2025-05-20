import os
import time
import socket
import struct
import threading 
from queue import Queue 
from collections import deque
import json
import shutil
from datetime import datetime

PUERTO = 9990
BROADCAST_ADDR = '192.168.235.255'
HEADER_SIZE = 100
RESPONSE_SIZE = 25
TIMEOUT = 5
BROADCAST_ID = b'\xff'*20  

#Código de Operaciones
ECHO = 0             
MENSAJE = 1 
ARCHIVO = 2 
CREAR_GRUPO = 3 
UNIRSE_A_GRUPO = 4
MENSAJE_GRUPAL = 5 

#Códigos de respuesta
OK = 0
PETICION_INVALIDA = 1
ERROR_INTERNO = 2

#Configuración                       
TIEMPO_INACTIVIDAD = TIMEOUT * 3 
INTERVALO_AUTODESCUBRIMIENTO = 15

mi_id = os.urandom(20)  
usuarios_conectados = {}  
historial_mensajes = {}
tcp_server_running = True
archivos_pendientes = {}

usuarios_lock = threading.Lock()
historial_lock = threading.Lock()
archivos_lock = threading.Lock()
cola_echo = Queue()
cola_mensajes = Queue()
cola_cuerpos = Queue()
cola_respuestas = Queue()
mensajes_recibidos = Queue()
cola_transferencias = Queue()

cola_creacion = Queue()
cola_union = Queue()

mensaje_headers = {}  
mensaje_headers_lock = threading.Lock()

grupos_creados = {}  
grupos_lock = threading.Lock()

udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
udp_socket.bind(('0.0.0.0', PUERTO))

def iniciar_servicios():
    """Inicia los hilos para los diferentes servicios"""
    threading.Thread(target=lector_udp, daemon=True).start()
    threading.Thread(target=procesar_echo, daemon=True).start()
    threading.Thread(target=procesar_cuerpos, daemon=True).start()
    threading.Thread(target=procesar_transferencias, daemon=True).start()
    threading.Thread(target=mostrar_mensajes_auto, daemon=True).start()
    threading.Thread(target=servidor_tcp, daemon=True).start()
    threading.Thread(target=autodescubrimiento_continuo, daemon=True).start()
    threading.Thread(target=verificar_inactividad, daemon=True).start()

    for _ in range(5):
        threading.Thread(target=procesar_mensajes, daemon=True).start()
        threading.Thread(target=procesar_creacion_grupos, daemon=True).start()
        threading.Thread(target=procesar_union_a_grupos, daemon=True).start()
    
def lector_udp():
    while tcp_server_running:
        try:
            data, addr = udp_socket.recvfrom(65507)
            if len(data) == 25:
                cola_respuestas.put(data)
            elif len(data) >= 41:
                op = data[40]
                if op == ECHO:
                    cola_echo.put((data, addr))
                elif op == MENSAJE:
                    cola_mensajes.put((data, addr))
                elif op == ARCHIVO:
                    cola_transferencias.put((data, addr))
                elif op == CREAR_GRUPO:
                    cola_creacion.put((data, addr))
                elif op == UNIRSE_A_GRUPO:
                    cola_union.put((data, addr))
                elif op == MENSAJE_GRUPAL:
                    cola_mensajes.put((data, addr)) 
                else:
                    print(f"[LCP] Operación desconocida: {op}")
            elif len(data) > 0:
                cola_cuerpos.put((data, addr))
        except Exception as e:
            print(f"[Error UDP lector]: {e}")

def procesar_echo():
    while True:
        data, addr = cola_echo.get()
        user_id_from = data[:20]
        if user_id_from == mi_id:
            continue
        ip_remota = addr[0]
        with usuarios_lock:
            ya_conocido = user_id_from in usuarios_conectados
            usuarios_conectados[user_id_from] = (ip_remota, time.time())
        if not ya_conocido:
            print(f"[LCP] Usuario descubierto: {user_id_from.hex()[:8]} desde IP {ip_remota}")
        if data[20:40] == BROADCAST_ID:
            respuesta = struct.pack('!B 20s 4s', OK, mi_id, b'\x00'*4)
            udp_socket.sendto(respuesta, addr)

def autodescubrimiento_continuo():
    """Envía periódicamente mensajes de autodescubrimiento"""
    while True:
        enviar_echo()
        time.sleep(INTERVALO_AUTODESCUBRIMIENTO)

def verificar_inactividad():
    """Verifica y elimina usuarios inactivos"""
    while True:
        time.sleep(TIEMPO_INACTIVIDAD)
        ahora = time.time()
        desconectados = []
        
        with usuarios_lock:
            for user_id, (ip, ultimo_contacto) in list(usuarios_conectados.items()):
                if ahora - ultimo_contacto > TIEMPO_INACTIVIDAD:
                    desconectados.append((user_id, ip))
                    del usuarios_conectados[user_id]
            
        for user_id, ip in desconectados:
            print(f"⚠️ Usuario {user_id.hex()[:8]} ({ip}) desconectado por inactividad")

def procesar_mensajes():
    while True:
        data, addr = cola_mensajes.get()
        try:
            user_id_from = data[:20]
            user_id_to = data[20:40]
            op_code = data[40]
            mensaje_id = data[41]
            longitud = int.from_bytes(data[42:50], 'big')
            
            if op_code == MENSAJE_GRUPAL:
                nombre_grupo = data[50:100].rstrip(b'\x00').decode('utf-8').strip().lower()

                with grupos_lock:
                    if nombre_grupo not in grupos_creados or mi_id not in grupos_creados[nombre_grupo]:
                        continue       

                with mensaje_headers_lock:
                    mensaje_headers[mensaje_id] = {
                        'es_broadcast': False,          
                        'grupo': nombre_grupo,
                        'from' : user_id_from
                    }
                respuesta = struct.pack('!B 20s 4s', OK, mi_id, b'\x00'*4)
                udp_socket.sendto(respuesta, addr)
            
            elif op_code == MENSAJE:
                if user_id_to == mi_id or user_id_to == BROADCAST_ID:
                    with mensaje_headers_lock:
                        mensaje_headers[mensaje_id] = {
                            'es_broadcast': user_id_to == BROADCAST_ID,
                            'from': user_id_from
                        }
                    respuesta = struct.pack('!B 20s 4s', OK, mi_id, b'\x00'*4)
                    udp_socket.sendto(respuesta, addr)
        except Exception as e:
            print(f"[Error al procesar mensaje]: {e}")
            
def procesar_cuerpos():
    while True:
        data, addr = cola_cuerpos.get()
        if len(data) < 2:
            continue
        try:
            mensaje_id = data[0]
            mensaje = data[1:].decode('utf-8', errors='ignore')
            if not mensaje.strip() or any(ord(c) < 32 for c in mensaje if c != '\n'):
                continue
        except:
            continue
            
        es_broadcast = False
        user_id_from = None
        nombre_grupo = None
        
        with mensaje_headers_lock:
            header_info = mensaje_headers.pop(mensaje_id, None) 
        
        if header_info:
            user_id_from = header_info['from']
            es_broadcast = header_info['es_broadcast']
            nombre_grupo = header_info.get('grupo') 
        else:
            with usuarios_lock:
                for uid, (ip, _) in usuarios_conectados.items():
                    if ip == addr[0]:
                        user_id_from = uid
                        break
        if user_id_from:
            hora = time.strftime("%H:%M:%S")
            with historial_lock:
                if nombre_grupo:
                    historial_mensajes.setdefault(nombre_grupo, deque(maxlen=50)).append((hora, mensaje, 'recibido', user_id_from))
                elif es_broadcast:
                    historial_mensajes.setdefault(BROADCAST_ID, deque(maxlen=50)).append((hora, mensaje, 'recibido', user_id_from))
                else:
                    historial_mensajes.setdefault(user_id_from, deque(maxlen=10)).append((hora, mensaje, 'recibido'))
                    mensajes_recibidos.put((user_id_from, hora, mensaje, False, None))
                    
            mensajes_recibidos.put((user_id_from, hora, mensaje, es_broadcast, nombre_grupo))
            if not es_broadcast and not nombre_grupo:
                respuesta = struct.pack('!B 20s 4s', OK, mi_id, b'\x00'*4)
                udp_socket.sendto(respuesta, addr)
                    
def procesar_transferencias():
    """Procesa los headers de transferencia de archivos"""
    while True:
        data, addr = cola_transferencias.get()
        try:
            user_id_from = data[:20]
            user_id_to = data[20:40]
            
            if user_id_to != mi_id and user_id_to != BROADCAST_ID:
                continue
                
            body_id = data[41:49]
            body_length = int.from_bytes(data[49:57], 'big')
            
            print(f"\n📁 Recibiendo archivo {body_id.hex()} de {user_id_from.hex()[:8]}")
            print(f"Tamaño: {body_length} bytes")
            
            with archivos_lock:
                archivos_pendientes[body_id] = {
                    'user_id': user_id_from,
                    'size': body_length,
                    'ip': addr[0],
                    'timestamp': time.time()}
        except Exception as e:
            print(f"[Error al procesar transferencia]: {e}")

def procesar_creacion_grupos():
    while True:
        data, addr = cola_creacion.get()
        try:
            user_id_from = data[:20]
            user_id_to = data[20:40]
            op_code = data[40]

            if op_code != CREAR_GRUPO:
                cola_mensaje.put((data, addr)) 
                time.sleep(0.01)
                continue
            
            nombre_grupo = data[41:].rstrip(b'\x00').decode('utf-8').strip()
            if not nombre_grupo:
                continue
            
            with grupos_lock:
                if nombre_grupo not in grupos_creados:
                    grupos_creados[nombre_grupo] = [user_id_from]
                    if user_id_from == mi_id:
                        print(f"✅ Has creado el grupo '{nombre_grupo}'")
                    else:
                        print(f"👥 Grupo '{nombre_grupo}' creado por {user_id_from.hex()[:8]}")
                else:
                    if user_id_from == mi_id:
                        print(f"⚠️ Ya existe un grupo con el nombre '{nombre_grupo}'")
        except Exception as e:
            print(f"[Error procesar creación grupo]: {e}")

def crear_grupo(nombre_grupo):
    try:
        nombre_bytes = nombre_grupo.encode('utf-8')
        if len(nombre_bytes) > 59:
            print("❌ Nombre de grupo demasiado largo (máx. 59 bytes).")
            return
        header = struct.pack('!20s 20s B', mi_id, BROADCAST_ID, CREAR_GRUPO) + nombre_bytes.ljust(59, b'\x00')
        udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))
    except Exception as e:
        print(f"❌ Error al crear grupo: {e}")

def unirse_a_grupo(nombre_grupo):
    try:
        nombre_bytes = nombre_grupo.encode('utf-8')
        if len(nombre_bytes) > 59:
            print("❌ Nombre de grupo demasiado largo (máx. 59 bytes).")
            return
        header = struct.pack('!20s 20s B', mi_id, BROADCAST_ID, UNIRSE_A_GRUPO) + nombre_bytes.ljust(59, b'\x00')
        udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))
    except Exception as e:
        print(f"❌ Error al unirse al grupo: {e}")

def procesar_union_a_grupos():
    while True:
        data, addr = cola_union.get()
        try:
            user_id_from = data[:20]
            op_code = data[40]

            if op_code != UNIRSE_A_GRUPO:
                cola_mensajes.put((data, addr))
                time.sleep(0.01)
                continue

            nombre_grupo = data[41:].rstrip(b'\x00').decode('utf-8').strip()
            if not nombre_grupo:
                continue

            with grupos_lock:
                if nombre_grupo in grupos_creados:
                    if user_id_from not in grupos_creados[nombre_grupo]:
                        grupos_creados[nombre_grupo].append(user_id_from)
                        print(f"✅ {user_id_from.hex()[:8]} se ha unido al grupo '{nombre_grupo}'")
                    else:
                        if user_id_from == mi_id:
                            print(f"Ya estás en el grupo '{nombre_grupo}'")
                else:
                    if user_id_from == mi_id:
                        print(f"⚠️ Grupo '{nombre_grupo}' no existe")
        except Exception as e:
            print(f"[Error procesar unión a grupo]: {e}")

def enviar_mensaje_grupal(nombre_grupo, mensaje):
    with grupos_lock:
        if nombre_grupo not in grupos_creados:
            print("❌ No existe ese grupo.")
            return
        elif mi_id not in grupos_creados[nombre_grupo]:
            print("❌ No perteneces a ese grupo.")
            return

    nombre_bytes = nombre_grupo.encode('utf-8')
    if len(nombre_bytes) > 50:
        print("❌ Nombre de grupo > 50 bytes.")
        return

    mensaje_bytes = mensaje.encode('utf-8')
    mensaje_id = int(time.time()*1000) % 256

    header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,
                            BROADCAST_ID,           
                            MENSAJE_GRUPAL,
                            mensaje_id,
                            len(mensaje_bytes).to_bytes(8,'big'),
                            nombre_bytes.ljust(50,b'\x00'))

    udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))
    cuerpo = struct.pack('!B', mensaje_id) + mensaje_bytes
    udp_socket.sendto(cuerpo, (BROADCAST_ADDR, PUERTO))
    print(f"📢 Mensaje enviado al grupo '{nombre_grupo}'.")

def servidor_tcp():
    """Servidor TCP para recibir archivos"""
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_socket.bind(('0.0.0.0', PUERTO))
    tcp_socket.listen(5)
    
    while tcp_server_running:
        try:
            conn, addr = tcp_socket.accept()
            threading.Thread(target=manejar_conexion_tcp, args=(conn, addr)).start()
        except Exception as e:
            print(f"[Error servidor TCP]: {e}")

def manejar_conexion_tcp(conn, addr):
    """Maneja una conexión TCP entrante para transferencia de archivos"""
    try:
        file_id = conn.recv(8)
        if len(file_id) != 8:
            conn.close()
            return
            
        with archivos_lock:
            archivo_info = archivos_pendientes.get(file_id)
            if not archivo_info:
                conn.close()
                return
                
        os.makedirs("recibidos", exist_ok=True)
        file_path = os.path.join("recibidos", f"{file_id.hex()}.bin")
    
        remaining_bytes = archivo_info['size']
        with open(file_path, 'wb') as f:
            while remaining_bytes > 0:
                chunk = conn.recv(min(4096, remaining_bytes))
                if not chunk:
                    break
                f.write(chunk)
                remaining_bytes -= len(chunk)
        if remaining_bytes == 0:
            print(f"✅ Archivo {file_id.hex()} recibido correctamente")
            conn.sendall(struct.pack('!B', OK))
        else:
            print(f"❌ Archivo {file_id.hex()} incompleto")
            conn.sendall(struct.pack('!B', ERROR_INTERNO))
        with archivos_lock:
            archivos_pendientes.pop(file_id, None)
    except Exception as e:
        print(f"[Error al recibir archivo]: {e}")
    finally:
        conn.close()

def enviar_echo():
    """Envía mensaje de descubrimiento a toda la red"""
    header = struct.pack('!20s 20s B B 8s 50s',
                        mi_id,     
                        BROADCAST_ID,
                        ECHO,             
                        0,     
                        b'\x00'*8,                
                        b'\x00'*50)             
    udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))

def enviar_mensaje(user_id_to, mensaje, es_broadcast=False):
    """Envía un mensaje a un usuario específico o a todos (broadcast)"""
    if not es_broadcast and user_id_to not in usuarios_conectados:
        print("❌ Usuario no encontrado en la lista de conectados.")
        return

    ip_destino = BROADCAST_ADDR if es_broadcast else usuarios_conectados[user_id_to][0]
    mensaje_bytes = mensaje.encode('utf-8')
    mensaje_id = int(time.time() * 1000) % 256

    try:
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id, 
                            BROADCAST_ID if es_broadcast else user_id_to,
                            MENSAJE, 
                            mensaje_id,
                            len(mensaje_bytes).to_bytes(8, 'big'),
                            b'\x00' * 50)
        udp_socket.sendto(header, (ip_destino, PUERTO))
        print("📤 Header enviado. Esperando OK..." if not es_broadcast else "📤 Header de broadcast enviado")

        if not es_broadcast:
            respuesta = cola_respuestas.get(timeout=5)
            if respuesta[0] != OK:
                print(f"❌ Error en respuesta al header: código {respuesta[0]}")
                return

        cuerpo = struct.pack('!B', mensaje_id) + mensaje_bytes
        udp_socket.sendto(cuerpo, (ip_destino, PUERTO))
        print("📤 Cuerpo enviado. Esperando OK..." if not es_broadcast else "📤 Cuerpo de broadcast enviado")

        if not es_broadcast:
            respuesta = cola_respuestas.get(timeout=5)
            if respuesta[0] == OK:
                print("✅ Mensaje enviado correctamente.")
                hora = time.strftime("%H:%M:%S")
                with historial_lock:
                    historial_mensajes.setdefault(user_id_to, deque(maxlen=10)).append((hora, mensaje, 'enviado'))
            else:
                print(f"❌ Error en respuesta al cuerpo: código {respuesta[0]}")
    except Exception as e:
        print(f"❌ Excepción al enviar mensaje: {e}")

def enviar_archivo(user_id_to, file_path):
    """Envía un archivo a otro usuario"""
    if user_id_to not in usuarios_conectados:
        print("❌ Usuario no encontrado en la lista de conectados.")
        return
        
    if not os.path.exists(file_path):
        print("❌ El archivo no existe.")
        return
        
    file_size = os.path.getsize(file_path)
    file_id = os.urandom(8)
    ip_destino = usuarios_conectados[user_id_to][0]
    
    try:
        header = struct.pack('!20s 20s B 8s 8s 16s',
                            mi_id,
                            user_id_to,
                            ARCHIVO,
                            file_id,
                            file_size.to_bytes(8, 'big'),
                            b'\x00'*16)
        udp_socket.sendto(header, (ip_destino, PUERTO))
        print("📤 Header de archivo enviado")
        
        print("🔌 Conectando para enviar archivo...")
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.settimeout(TIMEOUT)
        tcp_socket.connect((ip_destino, PUERTO))
        
        tcp_socket.sendall(file_id)
        
        with open(file_path, 'rb') as f:
            bytes_sent = 0
            while bytes_sent < file_size:
                chunk = f.read(4096)
                tcp_socket.sendall(chunk)
                bytes_sent += len(chunk)
                print(f"📤 Enviados {bytes_sent}/{file_size} bytes", end='\r')
                
        status = tcp_socket.recv(1)
        if status[0] == OK:
            print("\n✅ Archivo enviado correctamente (OK)")
        else:
            print(f"\n❌ Error al enviar archivo: código {status[0]}")
            
    except socket.timeout:
        print("\n❌ Tiempo de espera agotado")
    except Exception as e:
        print(f"\n❌ Error al enviar archivo: {e}")
    finally:
        tcp_socket.close()

def mostrar_mensajes_auto():
    while True:
        uid, hora, msg, es_broadcast, nombre_grupo = mensajes_recibidos.get()
        if es_broadcast:
            print(f"📢 [Broadcast] {hora} - {uid.hex()[:8]}: {msg}")
        else:
            print(f"📩 [Privado] {hora} - {uid.hex()[:8]}: {msg}")

def mostrar_historial(user_id):
    with historial_lock:
        if user_id not in historial_mensajes:
            print("\nNo hay historial de mensajes con este usuario.")
            return
            
        print(f"\n=== ÚLTIMOS 10 MENSAJES CON {user_id.hex()[:8]} ===")
        for hora, mensaje, tipo in historial_mensajes[user_id]:  
            prefix = "Tú:" if tipo == 'enviado' else "Ell@:"
            print(f"[{hora}] {prefix} {mensaje}")

def guardar_historial():
    """Guarda solo mensajes personales en JSON"""
    try:
        with historial_lock:
            historial_serializable = {
                key.hex(): list(value) for key, value in historial_mensajes.items() 
                if isinstance(key, bytes)  
            }
        
        with open("historial_personal.json", "w") as f:
            json.dump(historial_serializable, f)
    except Exception as e:
        print(f"⚠️ Error al guardar historial: {e}")

def cargar_historial():
    """Carga el historial personal desde JSON"""
    try:
        with open("historial_personal.json", "r") as f:
            historial_cargado = json.load(f)
        
        with historial_lock:
            for user_id_hex, mensajes in historial_cargado.items():
                user_id = bytes.fromhex(user_id_hex)
                historial_mensajes[user_id] = deque(mensajes, maxlen=10)
    except FileNotFoundError:
        print("🔍 No hay historial previo de mensajes personales.")
    except Exception as e:
        print(f"⚠️ Error al cargar historial: {e}")
        
def mostrar_menu():
    while True:
        print("\n=== MENÚ PRINCIPAL ===")
        print(f"Tu ID: {mi_id.hex()[:8]}")
        print("\n1. Listar usuarios conectados")
        print("2. Enviar mensaje a usuario")
        print("3. Enviar mensaje a todos (broadcast)")
        print("4. Enviar archivo a usuario")
        print("5. Crear grupo")
        print("6. Unirse a grupo existente")
        print("7. Enviar mensaje a grupo")
        print("8. Ver historial de mensajes con usuario")
        print("9. Salir")
        opcion = input("Opción: ").strip()
        
        if opcion == "1":
            with usuarios_lock:
                if not usuarios_conectados:
                    print("\nNo hay otros usuarios conectados actualmente.")
                else:
                    print("\n=== USUARIOS CONECTADOS ===")
                    for i, (uid, (ip, ultimo_contacto)) in enumerate(usuarios_conectados.items(), 1):
                        tiempo_desde_contacto = time.time() - ultimo_contacto
                        estado = "ACTIVO" if tiempo_desde_contacto < TIEMPO_INACTIVIDAD/2 else "INACTIVO"
                        print(f"{i}. ID: {uid.hex()[:8]} | IP: {ip} | Estado: {estado}")
        elif opcion == "2":
            usuarios = list(usuarios_conectados.keys())
            if not usuarios:
                print("\nNo hay usuarios conectados para enviar mensajes.")
                continue
                
            print("\n=== USUARIOS DISPONIBLES ===")
            for i, uid in enumerate(usuarios, 1):
                estado = "ACTIVO" if (time.time() - usuarios_conectados[uid][1]) < TIEMPO_INACTIVIDAD/2 else "INACTIVO"
                print(f"{i}. {uid.hex()[:8]} ({estado})")
                
            try:
                idx = int(input("\nSeleccione usuario #: ")) - 1
                if idx < 0 or idx >= len(usuarios):
                    print("❌ Número inválido.")
                    continue
                msg = input("Mensaje: ")
                enviar_mensaje(usuarios[idx], msg)
            except ValueError:
                print("❌ Entrada inválida. Ingresa un número válido.")
        elif opcion == "3":
            msg = input("\nMensaje broadcast: ")
            enviar_mensaje(BROADCAST_ID, msg, es_broadcast=True)
        elif opcion == "4":
            usuarios = list(usuarios_conectados.keys())
            if not usuarios:
                print("\nNo hay usuarios conectados para enviar archivos.")
                continue
                
            print("\n=== USUARIOS DISPONIBLES ===")
            for i, uid in enumerate(usuarios, 1):
                estado = "ACTIVO" if (time.time() - usuarios_conectados[uid][1]) < TIEMPO_INACTIVIDAD/2 else "INACTIVO"
                print(f"{i}. {uid.hex()[:8]} ({estado})")
                
            try:
                idx = int(input("\nSeleccione usuario #: ")) - 1
                if idx < 0 or idx >= len(usuarios):
                    print("❌ Número inválido.")
                    continue
                ruta = input("Ruta del archivo: ").strip()
                if not os.path.isfile(ruta):
                    print("❌ Archivo no encontrado o no es un archivo válido.")
                    continue
                enviar_archivo(usuarios[idx], ruta)
            except ValueError:
                print("❌ Entrada inválida. Ingresa un número válido.")
        elif opcion == "5":
            nombre_grupo = input("Ingrese nombre del nuevo grupo: ").strip()
            if nombre_grupo:
                crear_grupo(nombre_grupo)
            else:
                print("❌ Nombre de grupo vacío.")
        elif opcion == "6":
            with grupos_lock:
                if not grupos_creados:
                    print("📭 No hay grupos disponibles.")
                else:
                    print("📚 Grupos disponibles:")
                    for nombre in grupos_creados:
                        print(f" - {nombre}")
                    nombre_grupo = input("Ingresa el nombre del grupo al que deseas unirte: ").strip()
                    unirse_a_grupo(nombre_grupo)
        elif opcion == "7":
            with grupos_lock:
                if not grupos_creados:
                    print("📭 No hay grupos.")
                    continue
                print("=== TUS GRUPOS ===")
                for g in grupos_creados:
                    if mi_id in grupos_creados[g]:
                        print(" -", g)
            nombre = input("Grupo destino: ").strip()
            texto  = input("Mensaje: ")
            enviar_mensaje_grupal(nombre, texto)
            
        elif opcion == "8":
            usuarios = list(usuarios_conectados.keys())
            if not usuarios:
                print("\nNo hay usuarios conectados para ver historial.")
                continue
                
            print("\n=== USUARIOS DISPONIBLES ===")
            for i, uid in enumerate(usuarios, 1):
                estado = "ACTIVO" if (time.time() - usuarios_conectados[uid][1]) < TIEMPO_INACTIVIDAD/2 else "INACTIVO"
                print(f"{i}. {uid.hex()[:8]} ({estado})")
                
            try:
                idx = int(input("\nSeleccione usuario #: ")) - 1
                if idx < 0 or idx >= len(usuarios):
                    print("❌ Número inválido.")
                    continue
                mostrar_historial(usuarios[idx])
            except ValueError:
                print("❌ Entrada inválida. Ingresa un número válido.")
        elif opcion == "9":
            global tcp_server_running
            tcp_server_running = False
            print("\nSaliendo del programa...")
            break
        else:
            print("❌ Opción no válida. Intente nuevamente.")

if __name__ == '__main__':
    cargar_historial()
    print("Iniciando servicios...")
    iniciar_servicios()
    print("Servicios iniciados correctamente")
    print("El sistema ahora descubrirá usuarios automáticamente")
    try:
        mostrar_menu()
    finally:
        guardar_historial() 
    