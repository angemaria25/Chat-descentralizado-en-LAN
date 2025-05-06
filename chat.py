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
BROADCAST_ID = b'\xff'*20  # 20 bytes de 0xFF

mi_id = os.urandom(20)  
usuarios_conectados = {}  
mensajes_recibidos = Queue()
archivos_recibidos = Queue()
tcp_server_running = True

udp_socket = None
tcp_socket = None

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
                            b'\xff'*20,
                            0,             
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
        
        usuarios_conectados[user_id_from] = (addr[0], time.time())
        print(f"[LCP] Usuario descubierto: {user_id_from.hex()} desde {addr[0]}")
        
        respuesta = struct.pack('!B 20s 4s',
                                0,              
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
        if len(mensaje_bytes) > MAX_MSG_SIZE:
            print(f"[Error] Mensaje demasiado largo (máx {MAX_MSG_SIZE} bytes)")
            return False
        
        mensaje_id = int(time.time() * 1000) % 256 #1 byte
        
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,     
                            user_id_to,
                            1,             
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
            
def manejar_mensaje(data, addr):
    """Procesa mensaje recibido"""
    
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
        
            respuesta = struct.pack('!B 20s 4s', 0, mi_id, b'\x00'*4)
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

def enviar_mensaje_broadcast(mensaje):
    """Envía un mensaje a TODOS los usuarios con una sola transmisión"""
    try:
        mensaje_bytes = mensaje.encode('utf-8')
        mensaje_id = int(time.time() * 1000) % 256  
        
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,          
                            BROADCAST_ID,   
                            1,             
                            mensaje_id,     
                            len(mensaje_bytes).to_bytes(8, 'big'),
                            b'\x00'*50)
        
        cuerpo = struct.pack('!B', mensaje_id) + mensaje_bytes
        
        udp_socket.sendto(header + cuerpo, (BROADCAST_ADDR, PUERTO))
        print(f"[Broadcast] Mensaje enviado a toda la red: {mensaje}")
        
    except Exception as e:
        print(f"[Error] En broadcast: {e}")

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
                            2,                     
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
                        chunk = f.read(4096)
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
                    data = tcp_conn.recv(4096)
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
        
        respuesta = struct.pack('!B 20s 4s', 0, mi_id, b'\x00'*4)
        tcp_conn.sendall(respuesta)
        
        archivos_recibidos.put((addr[0], filename))
        
    except ConnectionAbortedError as e:
        print(f"[Info] {e}")
    except Exception as e:
        print(f"[Error] Al recibir archivo: {e}")
    
        if 'tcp_conn' in locals():
            try:
                respuesta = struct.pack('!B 20s 4s', 2, mi_id, b'\x00'*4)
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
    """Escucha mensajes UDP con manejo de cierre"""
    
    while tcp_server_running:
        try:
            data, addr = udp_socket.recvfrom(HEADER_SIZE)
            
            if not tcp_server_running:  
                break
            
            if len(data) >= 41:
                operation = data[40]
                
                if operation == 0:    
                    manejar_echo(data, addr)
                elif operation == 1: 
                    manejar_mensaje(data, addr)
                elif operation == 2:  
                    print("[LCP] Header de archivo recibido")
        except socket.error as e:
            if tcp_server_running:  # 
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

#######################
# Interfaz de usuario.
#######################
def mostrar_menu():
    """Muestra el menú principal y maneja las opciones"""
    
    while True:
        print("\n--- Chat LCP ---")
        print("1. Listar usuarios")
        print("2. Enviar mensaje")
        print("3. Enviar archivo")
        print("4. Ver mensajes")
        print("5. Ver archivos")
        print("6. Salir")
        
        opcion = input("Seleccione: ")
        
        if opcion == "1":
            listar_usuarios()
        elif opcion == "2":
            enviar_mensaje_menu()
        elif opcion == "3":
            enviar_archivo_menu()
        elif opcion == "4":
            ver_mensajes()
        elif opcion == "5":
            ver_archivos()
        elif opcion == "6":
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
    """Muestra mensajes recibidos"""
    
    print("\nMensajes recibidos:")
    
    while not mensajes_recibidos.empty():
        uid, hora,  msg = mensajes_recibidos.get()
        print(f"{hora} - {uid.hex()[:8]}...: {msg}")
        
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