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
def enviar_mensajes_texto(user_id_to, mensaje):
    """Envía un mensaje de texto según LCP (Operación 1)"""
    
    if user_id_to not in usuarios_conectados:
        print(f"[Error] Usuario {user_id_to.hex()} no encontrado")
        return False
    
    try:
        ip_destino = usuarios_conectados[user_id_to][0]
        mensaje_bytes = mensaje.encode('utf-8')
        mensaje_id = int(time.time() * 1000) % 256 #1 byte
        
        #Fase 1: Enviar header del mensaje.
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,     
                            user_id_to,
                            1,             
                            mensaje_id,     
                            len(mensaje_bytes).to_bytes(8, 'big'),                
                            b'\x00'*50)  
        
        udp_socket.sendto(header, (ip_destino, PUERTO))
        
        #Esperar respuesta según especificación
        udp_socket.settimeout(TIMEOUT)
        
        try:
            respuesta, _ = udp_socket.recvfrom(RESPONSE_SIZE)
            status = respuesta[0]
            
            if status != 0:  # 0 = OK 
                print(f"[Error] Receptor reportó error: {status}")
                return False
        
        
            #Fase 2: Enviar cuerpo del mensaje.
            cuerpo = struct.pack('!Q', mensaje_id) + mensaje_bytes
            udp_socket.sendto(cuerpo, (ip_destino, PUERTO))
            
            #Esperar confirmación final
            respuesta, _ = udp_socket.recvfrom(RESPONSE_SIZE)
            status = respuesta[0]
            
            if status == 0:
                print(f"[LCP] Mensaje enviado a {user_id_to.hex()}")
                return True
            else:
                print(f"[Error] Confirmación fallida: {status}")
                return False
            
        except socket.timeout:
            print("[Error] Tiempo de espera agotado")
            return False
        finally:
            udp_socket.settimeout(None)
            
    except Exception as e:
        print(f"[Error] Al enviar mensaje: {e}")
        return False
            
            
    
def manejar_mensaje(data, addr):
    """Procesa mensaje recibido"""
    
    try:
        user_id_from = data[:20]
        user_id_to = data[20:40]
        operation = data[40]
        body_id = data[41]
        body_length = int.from_bytes(data[42:50], 'big')
        
        if user_id_to != mi_id and user_id_to != b'\xff'*20:
            return
        
        usuarios_conectados[user_id_from] = (addr[0], time.time())
        
        if operation_code == 1: 
            respuesta = struct.pack('!B 20s 4s',
                                    0,              
                                    mi_id,          
                                    b'\x00'*4)      
            
            udp_socket.sendto(respuesta, addr)
            
            # Recibir cuerpo del mensaje
            udp_socket.settimeout(TIMEOUT)
            
            try:
                cuerpo_data, _ = udp_socket.recvfrom(body_length + 8) 
                
                recibido_id = struct.unpack('!Q', cuerpo_data[:8])[0]
                if (recibido_id % 256) != body_id:
                    print("[Error] ID de mensaje no coincide")
                    return
                
                mensaje = cuerpo_data[8:].decode('utf-8')
                timestamp = time.strftime("%H:%M:%S")
                mensajes_recibidos.put((user_id_from, timestamp, mensaje))
                
                print(f"\n[LCP] Mensaje de {user_id_from.hex()}: {mensaje}\n> ", end="")
            
                confirmacion = struct.pack('!B 20s 4s',
                                            0,         
                                            mi_id,
                                            b'\x00'*4)
                
                udp_socket.sendto(confirmacion, addr)
                
            except socket.timeout:
                print("[Error] Tiempo de espera para cuerpo del mensaje")
            finally:
                udp_socket.settimeout(None)
                
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
        
        
        #Fase 1: Enviar header por UDP.
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,                 
                            user_id_to,            
                            2,                     
                            file_id,         
                            file_size.to_bytes(8, 'big'),  
                            b'\x00'*50)           
        
        udp_socket.sendto(header, (ip_destino, PUERTO))
        
        #Esperar respuesta UDP según LCP
        udp_socket.settimeout(TIMEOUT)
        
        try:
            respuesta, _ = udp_socket.recvfrom(RESPONSE_SIZE)
            status = respuesta[0]
            
            if status != 0:
                print(f"[Error] Receptor reportó error: {status}")
                return False
            
            #Fase 2: Enviar archivo por TCP.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_send_socket:
                tcp_send_socket.settimeout(TIMEOUT * 3) #Más tiempo para archivos
                tcp_send_socket.connect((ip_destino, PUERTO))
                
                #Enviar ID de archivo y contenido
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
                
                #Esperar confirmación final por TCP
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
        tcp_conn.settimeout(TIMEOUT * 3)  #Más tiempo para archivos
        
        #Recibir ID de archivo (8 bytes)
        file_id_data = tcp_conn.recv(8)
        
        if len(file_id_data) != 8:
            raise ValueError("ID de archivo incompleto")
        
        file_id = int.from_bytes(file_id_data, 'big')
        
        #Crear directorio para archivos recibidos
        os.makedirs("archivos_recibidos", exist_ok=True)
        filename = f"archivos_recibidos/{file_id}_{int(time.time())}.dat"
        
        #Recibir archivo
        with open(filename, 'wb') as f:
            total_recibido = 0
            while True:
                data = tcp_conn.recv(4096)
                if not data:
                    break
                f.write(data)
                total_recibido += len(data)
                print(f"\r[LCP] Recibidos {total_recibido/1024:.1f}KB", end="")
        
        print(f"\n[LCP] Archivo guardado como {filename}")
        
        #Enviar confirmación por TCP 
        respuesta = struct.pack('!B 20s 4s',
                                0,              
                                mi_id,
                                b'\x00'*4)
            
        tcp_conn.sendall(respuesta)
        tcp_conn.close()
        
        archivos_recibidos.put((addr[0], filename))
        
    except Exception as e:
        print(f"[Error] Al recibir archivo: {e}")
        if 'tcp_conn' in locals():
            respuesta = struct.pack('!B 20s 4s',
                                    2,          
                                    mi_id,
                                    b'\x00'*4)
            tcp_conn.sendall(respuesta)
            tcp_conn.close()

##############################           
#Funciones de red principales. 
###############################
def escuchar_udp():
    """Escucha mensajes UDP entrantes en un bucle infinito"""
    
    while True:
        try:
            data, addr = udp_socket.recvfrom(HEADER_SIZE)
            
            #El header mínimo tiene 41 bytes (los otros campos son opcionales)
            if len(data) >= 41:
                operation = data[40]
                
                if operation_code == 0:    #Echo
                    manejar_echo(data, addr)
                elif operation_code == 1:  #Mensaje
                    manejar_mensaje(data, addr)
                elif operation_code == 2:   #File
                    print("[LCP] Header de archivo recibido")
                
        except Exception as e:
            print(f"[Error] Al recibir mensaje UDP: {e}")

def escuchar_tcp():
    """Acepta conexiones TCP para transferencia de archivos"""
    
    while tcp_server_running:
        try:
            conn, addr = tcp_socket.accept()
            threading.Thread(target=manejar_archivo, args=(conn, addr)).start()
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
        time.sleep(15)

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
    for i, (uid, _) in enumerate(usuarios, 1):
        print(f"{i}. {uid.hex()[:8]}...")

    try:
        seleccion = int(input("Número: ")) - 1
        if 0 <= seleccion < len(usuarios):
            mensaje = input("Mensaje: ")
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
    
    global tcp_server_running
    
    tcp_server_running = False
    
    if udp_socket: 
        udp_socket.close()
        
    if tcp_socket: 
        tcp_socket.close()
    
    print("Saliendo...")

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