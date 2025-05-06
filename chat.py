import os
import time
import socket
import struct
import threading 
from queue import Queue 

PUERTO = 9990
BROADCAST_ADDR = '255.255.255.255'
HEADER_SIZE = 100
RESPONSE_SIZE = 25
TIMEOUT = 5
BROADCAST_ID = b'\xff'*20  

#Código de Operaciones
ECHO = 0             
MENSAJE = 1          
ARCHIVO = 2          
CREAR_GRUPO = 3      
UNIRSE_GRUPO = 4     
MENSAJE_GRUPAL = 5   

#Códigos de respuesta
OK = 0
PETICION_INVALIDA = 1
ERROR_INTERNO = 2

#Configuración
TAM_MAX_MSJ = 1024              
TAM_CHUNK = 4096                 
INTERVALO_ECO = 10               
TIEMPO_INACTIVIDAD = TIMEOUT * 3 

mi_id = os.urandom(20)  
usuarios_conectados = {}  
mensajes_recibidos = Queue()
archivos_recibidos = Queue()
tcp_server_running = True

grupos = {} 
usuarios_grupos = {}

udp_socket = None
tcp_socket = None

usuarios_lock = threading.Lock()

def iniciar_sockets():
    """Configura y retorna los sockets UDP y TCP"""
    
    global udp_socket, tcp_socket
    
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1) 
        udp_socket.bind(('0.0.0.0', PUERTO))
        
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_socket.bind(('0.0.0.0', PUERTO))
        tcp_socket.listen(5)
        
        print(f"[Red] Sockets iniciados en puerto {PUERTO}")
        print(f"Tu ID: {mi_id.hex()}")
        return udp_socket, tcp_socket
    
    except Exception as e:
        print(f"[Error] No se pudieron iniciar los sockets: {e}")
        raise
    
def iniciar_servicios():
    """Inicia los hilos para los diferentes servicios LCP"""
    
    threading.Thread(target=escuchar_udp, daemon=True).start()
    threading.Thread(target=escuchar_tcp, daemon=True).start()
    threading.Thread(target=enviar_echos_periodicos, daemon=True).start()
    threading.Thread(target=verificar_inactivos, daemon=True).start()

###########################################
#Operación 0: Echo-Reply (Descubrimiento).
###########################################
def enviar_echo():
    """Envía mensaje de descubrimiento a toda la red según LCP (Operación 0)"""
    
    try:
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,     
                            BROADCAST_ID,
                            ECHO,             
                            0,     
                            b'\x00'*8,                
                            b'\x00'*50)             
        
        udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))
        
    except Exception as e:
        print(f"[Error] Al enviar echo: {e}")

def manejar_echo(data, addr):
    """Procesa mensaje Echo recibido según LCP"""
    
    try:
        user_id_from = data[:20]
        
        if user_id_from == mi_id:
            return
        
        #Zona crítica (protegemos el acceso al diccionario)
        with usuarios_lock:
            usuarios_conectados[user_id_from] = (addr[0], time.time())
        
        print(f"[LCP] Usuario descubierto: {user_id_from.hex()} desde {addr[0]}")
        
        respuesta = struct.pack('!B 20s 4s',
                                OK,              
                                mi_id,          
                                b'\x00'*4)     
        
        udp_socket.sendto(respuesta, addr)
            
    except Exception as e:
        print(f"[Error] Al procesar Echo: {e}")
        
################################
#Operación 1: Message-Response.
################################
def enviar_mensaje(user_id_to, mensaje):
    """Envía un mensaje de texto según LCP (Operación 1)"""
    
    if user_id_to not in usuarios_conectados:
        print(f"[Error] Usuario {user_id_to.hex()} no encontrado")
        return False
    
    try:
        ip_destino = usuarios_conectados[user_id_to][0]
        mensaje_bytes = mensaje.encode('utf-8')
        
        MAX_MSG_SIZE = 1024 
        if len(mensaje_bytes) > TAM_MAX_MSJ:
            print(f"[Error] Mensaje demasiado largo (máx {TAM_MAX_MSJ} bytes)")
            return False
        
        mensaje_id = int(time.time() * 1000) % 256 #1 byte
        
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,     
                            user_id_to,
                            MENSAJE,             
                            mensaje_id,     
                            len(mensaje_bytes).to_bytes(8, 'big'),                
                            b'\x00'*50)  
        
        udp_socket.sendto(header, (ip_destino, PUERTO))
    
        udp_socket.settimeout(TIMEOUT)
        
        try:
            respuesta, _ = udp_socket.recvfrom(RESPONSE_SIZE)
            status = respuesta[0]
            
            if status != 0:  
                print("[Error] Receptor no aceptó el mensaje")
                return False
        except socket.timeout:
            print("[Error] Tiempo de espera para confirmación de header")
            return False
        
        cuerpo = struct.pack('!B', mensaje_id) + mensaje_bytes
        udp_socket.sendto(cuerpo, (ip_destino, PUERTO))
            
        try:
            respuesta, _ = udp_socket.recvfrom(RESPONSE_SIZE)
            status = respuesta[0]
            
            if status == 0:
                print(f"[Éxito] Mensaje enviado a {user_id_to.hex()}")
                return True
            else:
                print("[Error] Confirmación fallida")
                return False
        except socket.timeout:
            print("[Error] Tiempo de espera  para confirmación final agotado")
            return False
            
    except Exception as e:
        print(f"[Error] Al enviar mensaje: {e}")
        return False
    finally:
        udp_socket.settimeout(None)
        
def enviar_mensaje_broadcast(mensaje):
    """Envía un mensaje a TODOS los usuarios con una sola transmisión"""
    try:
        mensaje_bytes = mensaje.encode('utf-8')
        mensaje_id = int(time.time() * 1000) % 256  
        
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,          
                            BROADCAST_ID,   
                            MENSAJE,             
                            mensaje_id,     
                            len(mensaje_bytes).to_bytes(8, 'big'),
                            b'\x00'*50)
        
        cuerpo = struct.pack('!B', mensaje_id) + mensaje_bytes
        
        udp_socket.sendto(header + cuerpo, (BROADCAST_ADDR, PUERTO))
        print(f"[Broadcast] Mensaje enviado a toda la red: {mensaje}")
        
    except Exception as e:
        print(f"[Error] En broadcast: {e}")


def manejar_mensaje(data, addr):
    """Procesa mensajes recibidos"""
    
    try:
        user_id_from = data[:20]
        user_id_to = data[20:40]
        operation = data[40]
        
        if operation == 1:
            es_broadcast = (user_id_to == BROADCAST_ID)
            
            if not es_broadcast:
                respuesta = struct.pack('!B 20s 4s', 0, mi_id, b'\x00'*4)
                udp_socket.sendto(respuesta, addr)
                
            body_id = data[41]
            body_length = int.from_bytes(data[42:50], 'big')
        
            respuesta = struct.pack('!B 20s 4s', OK, mi_id, b'\x00'*4)
            udp_socket.sendto(respuesta, addr)
            
            udp_socket.settimeout(TIMEOUT)
            cuerpo_data, _ = udp_socket.recvfrom(body_length + 1)  
        
            recibido_id = cuerpo_data[0] 
            if recibido_id != body_id:
                print("[Error] ID de mensaje no coincide")
                return
            
            mensaje = cuerpo_data[1:].decode('utf-8') 
            
            mensajes_recibidos.put((user_id_from, time.strftime("%H:%M:%S"), mensaje, es_broadcast))
            
            if not es_broadcast:
                confirmacion = struct.pack('!B 20s 4s', 0, mi_id, b'\x00'*4)
                udp_socket.sendto(confirmacion, addr)
                
    except Exception as e:
        print(f"[Error] Al procesar mensaje: {e}")

########################################################
#Operación 2: Send File-Ack (Transferencia de archivos).
#########################################################
def enviar_archivo(user_id_to, filepath):
    """Envía un archivo a otro usuario"""
    
    if user_id_to not in usuarios_conectados:
        print(f"[Error] Usuario {user_id_to.hex()} no encontrado")
        return False
    
    if not os.path.exists(filepath):
        print(f"[Error] Archivo no encontrado: {filepath}")
        return False
    
    try:
        ip_destino = usuarios_conectados[user_id_to][0]
        file_size = os.path.getsize(filepath)
        file_id = int(time.time() * 1000) % 256
        
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,                 
                            user_id_to,            
                            ARCHIVO,                     
                            file_id,         
                            file_size.to_bytes(8, 'big'),  
                            b'\x00'*50)           
        
        udp_socket.sendto(header, (ip_destino, PUERTO))
        
        udp_socket.settimeout(TIMEOUT)
        
        try:
            respuesta, _ = udp_socket.recvfrom(RESPONSE_SIZE)
            status = respuesta[0]
            
            if status != 0:
                print(f"[Error] Receptor reportó error: {status}")
                return False
            
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_send_socket:
                tcp_send_socket.settimeout(TIMEOUT * 3) #Más tiempo para archivos
                tcp_send_socket.connect((ip_destino, PUERTO))
                
                with open(filepath, 'rb') as f:
                    tcp_send_socket.sendall(file_id.to_bytes(8, 'big'))
                    
                    #Enviar archivo en chunks
                    total_sent = 0
                    while True:
                        chunk = f.read(TAM_CHUNK)
                        if not chunk:
                            break
                        tcp_send_socket.sendall(chunk)
                        total_sent += len(chunk)
                        print(f"\r[LCP] Enviados {total_sent/1024:.1f}KB/{file_size/1024:.1f}KB", end="")
                
                confirmacion = tcp_send_socket.recv(RESPONSE_SIZE)
                status = confirmacion[0]
                
                if status == 0:
                    print(f"\n[LCP] Archivo enviado a {user_id_to.hex()}")
                    return True
                else:
                    print(f"\n[Error] Confirmación fallida: {status}")
                    return False
                
        except socket.timeout:
            print("[Error] Tiempo de espera agotado")
            return False
        finally:
            udp_socket.settimeout(None)   
            
    except Exception as e:
        print(f"[Error] Al enviar archivo: {e}")
        return False
    
def manejar_archivo(tcp_conn, addr):
    """Procesa un archivo entrante de otro usuario"""
    
    try:
        tcp_conn.settimeout(1)
        
        file_id_data = b''
        start_time = time.time()
        
        while len(file_id_data) < 8 and tcp_server_running:
            try:
                chunk = tcp_conn.recv(8 - len(file_id_data))
                if not chunk:
                    raise ConnectionError("Conexión cerrada por el remitente")
                file_id_data += chunk
            except socket.timeout:
                continue  
        
        if not tcp_server_running:
            raise ConnectionAbortedError("Servidor deteniéndose")
            
        if len(file_id_data) != 8:
            raise ValueError("ID de archivo incompleto")
        
        file_id = int.from_bytes(file_id_data, 'big')
        
        os.makedirs("archivos_recibidos", exist_ok=True)
        filename = f"archivos_recibidos/{file_id}_{int(time.time())}.dat"
        
        with open(filename, 'wb') as f:
            total_recibido = 0
            while tcp_server_running:
                try:
                    data = tcp_conn.recv(TAM_CHUNK)
                    if not data:
                        break  
                    f.write(data)
                    total_recibido += len(data)
                    print(f"\r[LCP] Recibidos {total_recibido/1024:.1f}KB", end="")
                except socket.timeout:
                    continue  
                
        if not tcp_server_running:
            os.remove(filename)  
            raise ConnectionAbortedError("Transferencia cancelada por cierre del servidor")
            
        print(f"\n[LCP] Archivo guardado como {filename}")
        
        respuesta = struct.pack('!B 20s 4s', OK, mi_id, b'\x00'*4)
        tcp_conn.sendall(respuesta)
        
        archivos_recibidos.put((addr[0], filename))
        
    except ConnectionAbortedError as e:
        print(f"[Info] {e}")
    except Exception as e:
        print(f"[Error] Al recibir archivo: {e}")
    
        if 'tcp_conn' in locals():
            try:
                respuesta = struct.pack('!B 20s 4s', ERROR_INTERNO, mi_id, b'\x00'*4)
                tcp_conn.sendall(respuesta)
            except:
                pass  
    finally:
        if 'tcp_conn' in locals():
            try:
                tcp_conn.close()
            except:
                pass

##############################           
#Funciones de red principales. 
###############################
def escuchar_udp():
    """Escucha mensajes UDP y crea un hilo para cada mensaje"""
    
    while tcp_server_running:
        try:
            data, addr = udp_socket.recvfrom(HEADER_SIZE)
            
            if not tcp_server_running:  
                break
            
            if len(data) >= 41:
                operation = data[40]
                
                if operation == ECHO:    
                    manejar_echo(data, addr)
                elif operation == MENSAJE: 
                    threading.Thread(
                        target=manejar_mensaje,
                        args=(data, addr),
                        daemon=True
                    ).start()
                elif operation == ARCHIVO:  
                    print("[LCP] Header de archivo recibido")
                elif operation == CREAR_GRUPO:  
                    threading.Thread(
                        target=manejar_creacion_grupo,
                        args=(data, addr),
                        daemon=True
                    ).start()
                elif operation == UNIRSE_GRUPO:  
                    threading.Thread(
                        target=manejar_suscripcion_grupo,
                        args=(data, addr),
                        daemon=True
                    ).start()
                elif operation == MENSAJE_GRUPAL: 
                    threading.Thread(
                        target=manejar_mensaje_grupal,
                        args=(data, addr),
                        daemon=True
                    ).start()
                else:
                    print(f"[LCP] Operación desconocida: {operation}")
        except socket.error as e:
            if tcp_server_running:  
                print(f"[Error] UDP: {e}")
            continue
        except Exception as e:
            print(f"[Error] Inesperado en UDP: {e}")

def escuchar_tcp():
    """Acepta conexiones TCP para transferencia de archivos"""
    
    while tcp_server_running:
        try:
            tcp_socket.settimeout(1)
            conn, addr = tcp_socket.accept()
            threading.Thread(target=manejar_archivo, args=(conn, addr)).start()
        except socket.timeout:
            continue
        except Exception as e:
            if tcp_server_running:
                print(f"[Error] En conexión TCP: {e}")

def verificar_inactivos():
    """Elimina usuarios inactivos según timeout"""
    
    while True:
        time.sleep(10)
        ahora = time.time()
        inactivos = [uid for uid, (_, last) in usuarios_conectados.items() 
                    if ahora - last > TIMEOUT * 3]
        
        for uid in inactivos:
            print(f"[LCP] Usuario {uid.hex()} eliminado por inactividad")
            del usuarios_conectados[uid]

def enviar_echos_periodicos():
    """Envía mensajes Echo periódicamente para descubrimiento"""
    
    while True:
        enviar_echo()
        time.sleep(10)
        
###################
#Mensajes Grupales.
###################
def manejar_creacion_grupo(data, addr):
    """Procesa solicitud de creación de nuevo grupo"""
    
    try:
        user_id_from = data[:20]
        nombre_length = int.from_bytes(data[42:50], 'big')
        nombre_grupo = data[HEADER_SIZE:HEADER_SIZE+nombre_length].decode('utf-8')
        
        with usuarios_lock:
            group_id = os.urandom(10)
            grupos[group_id] = {
                'nombre': nombre_grupo,
                'creador': user_id_from,
                'miembros': {user_id_from}
            }
            usuarios_grupos[user_id_from].add(group_id)
        
        #Envía confirmación con el group_id
        respuesta = struct.pack('!B 20s 10s 4s', 0, mi_id, group_id, b'\x00'*4)
        udp_socket.sendto(respuesta, addr)
        
    except Exception as e:
        print(f"[Error] Al crear grupo: {e}")
        respuesta = struct.pack('!B 20s 10s 4s', 2, mi_id, b'\x00'*10, b'\x00'*4)
        udp_socket.sendto(respuesta, addr)
        
        
def manejar_suscripcion_grupo(data, addr):
    """Procesa solicitud de unirse a un grupo existente"""
    
    try:
        user_id_from = data[:20]
        group_id = data[50:60]  
        with usuarios_lock:
            if group_id in grupos:
                grupos[group_id]['miembros'].add(user_id_from)
                usuarios_grupos[user_id_from].add(group_id)
                status = 0  
            else:
                status = 1  
        
        respuesta = struct.pack('!B 20s 4s', status, mi_id, b'\x00'*4)
        udp_socket.sendto(respuesta, addr)
        
    except Exception as e:
        print(f"[Error] En suscripción a grupo: {e}")
        
def manejar_mensaje_grupal(data, addr):
    """Procesa mensaje destinado a un grupo"""
    
    try:
        user_id_from = data[:20]
        group_id = data[50:60]
        body_length = int.from_bytes(data[42:50], 'big')
        
        cuerpo_data, _ = udp_socket.recvfrom(body_length)
        mensaje = cuerpo_data.decode('utf-8')
        
        with usuarios_lock:
            if group_id not in grupos:
                print(f"[Grupo] ID {group_id.hex()} no existe")
                return
                
            if user_id_from not in grupos[group_id]['miembros']:
                print(f"[Grupo] Usuario {user_id_from.hex()} no es miembro")
                return
            
            nombre_grupo = grupos[group_id]['nombre']
            miembros = grupos[group_id]['miembros'].copy()
            
        mensaje_grupal = f"[GRUPO:{nombre_grupo}] {mensaje}"
        
        #Enviar a cada miembro conectado (excepto remitente)
        for member in miembros:
            if member == user_id_from:
                continue  #Saltar al remitente
                
            if member in usuarios_conectados:
                ip_destino = usuarios_conectados[member][0]
                threading.Thread(
                    target=enviar_mensaje,
                    args=(member, mensaje_grupal),
                    daemon=True
                ).start()
                print(f"[Grupo] Mensaje enviado a {member.hex()[:8]}...")
            else:
                print(f"[Grupo] Miembro {member.hex()[:8]}... no conectado")
        
    except socket.timeout:
        print("[Error] Tiempo de espera para recibir cuerpo del mensaje")
    except Exception as e:
        print(f"[Error] Al procesar mensaje grupal: {e}")

#####################
#Interfaz de usuario.
#####################
def mostrar_menu():
    """Muestra el menú principal y maneja las opciones"""
    
    while True:
        print("\n--- Chat LAN ---")
        print("1. Listar usuarios")
        print("2. Enviar mensaje directo")
        print("3. Enviar mensaje a TODOS (broadcast)")
        print("4. Enviar archivo")
        print("5. Ver mensajes")
        print("6. Ver archivos")
        print("7. Salir")
        
        opcion = input("Seleccione: ")
        
        if opcion == "1":
            listar_usuarios()
        elif opcion == "2":
            enviar_mensaje_menu()
        elif opcion == "3":
            mensaje = input("Mensaje para TODOS los usuarios: ")
            enviar_mensaje_broadcast(mensaje)
        elif opcion == "4":
            enviar_archivo_menu()
        elif opcion == "5":
            ver_mensajes()
        elif opcion == "6":
            ver_archivos()
        elif opcion == "7":
            salir()
            break
        else:
            print("Opción inválida")
            
def listar_usuarios():
    """Lista de usuarios conectados"""
    
    print("\nUsuarios conectados:")
    if not usuarios_conectados:
        print("No hay otros usuarios conectados")
        return
    
    for i, (uid, (ip, last)) in enumerate(usuarios_conectados.items(), 1):
        inactivo = int(time.time() - last)
        print(f"{i}. ID: {uid.hex()[:8]}... | IP: {ip} | Inactivo: {inactivo}s")
    
def enviar_mensaje_menu():
    """Interfaz para enviar mensajes"""
    
    if not usuarios_conectados:
        print("No hay usuarios conectados")
        return
        
    print("\nSeleccione usuario:")
    
    usuarios = list(usuarios_conectados.items())
    
    for i, (uid, (ip, _)) in enumerate(usuarios, 1):
        print(f"{i}. {uid.hex()[:8]}... (IP: {ip})")

    try:
        seleccion = input("Número: ").strip()
        
        if not seleccion.isdigit():
            raise ValueError("Debe ingresar un número")
        
        seleccion = int(seleccion) - 1
        
        if 0 <= seleccion < len(usuarios):
            mensaje = input("Mensaje: ").strip()
            
            if not mensaje:
                print("El mensaje no puede estar vacío")
                return
            
            if enviar_mensaje(usuarios[seleccion][0], mensaje):
                print("Mensaje enviado")
            else:
                print("Error al enviar mensaje")
        else:
            print("Selección inválida")
    except ValueError:
        print("Entrada inválida")

def enviar_archivo_menu():
    """Interfaz para enviar archivos"""
    
    if not usuarios_conectados:
        print("No hay usuarios conectados")
        return
        
    print("\nSeleccione usuario:")
    usuarios = list(usuarios_conectados.items())
    for i, (uid, _) in enumerate(usuarios, 1):
        print(f"{i}. {uid.hex()[:8]}...")
    
    try:
        seleccion = int(input("Número: ")) - 1
        if 0 <= seleccion < len(usuarios):
            ruta = input("Ruta del archivo: ")
            if enviar_archivo(usuarios[seleccion][0], ruta):
                print("Archivo enviado con éxito")
            else:
                print("Error al enviar archivo")
        else:
            print("Selección inválida")
            
    except ValueError:
        print("Entrada inválida")
        
def ver_mensajes():
    """Muestra mensajes indicando si son broadcast"""
    
    print("\nMensajes recibidos:")
    
    while not mensajes_recibidos.empty():
        uid, hora,  msg, es_broadcast = mensajes_recibidos.get()
        tipo = " (BROADCAST)" if es_broadcast else ""
        print(f"{hora} - {uid.hex()[:8]}...{tipo}: {msg}")
        
def ver_archivos():
    """Muestra archivos recibidos"""
    
    print("\nArchivos recibidos:")
    
    while not archivos_recibidos.empty():
        ip, filename = archivos_recibidos.get()
        print(f"De {ip}: {filename} ({os.path.getsize(filename)/1024:.1f} KB)")

def salir():
    """Cierra la aplicación"""
    
    global tcp_server_running, udp_socket, tcp_socket
    
    print("Cerrando sockets y terminando hilos...")
    tcp_server_running = False
    
    #Limpiar estructuras de grupos
    grupos.clear()
    usuarios_grupos.clear()
    
    if tcp_socket:
        try:
            #Crea una conexión temporal para desbloquear el accept()
            temp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            temp_socket.connect(('localhost', PUERTO))
            temp_socket.close()
            
            tcp_socket.close()
        except Exception as e:
            print(f"[Error] Al cerrar socket TCP: {e}")
            
    if udp_socket:
        try:
            udp_socket.close()
        except Exception as e:
            print(f"[Error] Al cerrar socket UDP: {e}")
    
    time.sleep(0.5)
    print("Saliendo del programa...")
    os._exit(0)  #Fuerza la salida de todos los hilos

if __name__ == "__main__":
    try:
        print("Iniciando cliente LCP...")
        
        iniciar_sockets()
        iniciar_servicios()
        mostrar_menu()
        
    except KeyboardInterrupt:
        print("\nInterrumpido por usuario")
        
    except Exception as e:
        print(f"Error fatal: {e}")
    finally:
        salir()