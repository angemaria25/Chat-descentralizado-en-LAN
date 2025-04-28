import os
import time
import socket
import struct
import threading 
from queue import Queue 

PUERTO = 9990
BROADCAST_ADDR = '255.255.255.255'
TIMEOUT = 10

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
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1) 
        udp_socket.bind(('0.0.0.0', PUERTO))
        
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.bind(('0.0.0.0', PUERTO))
        tcp_socket.listen(5)
        
        print(f"[Red] Sockets iniciados en puerto {PUERTO}")
        return udp_socket, tcp_socket
    
    except Exception as e:
        print(f"[Error] No se pudieron iniciar los sockets: {e}")
        raise
    
def iniciar_servicios():
    """Inicia los hilos para los diferentes servicios"""
    
    threading.Thread(target=enviar_echos_periodicos, daemon=True).start()

###########################################
#Operación 0: Echo-Reply (Descubrimiento).
###########################################
def enviar_echo():
    """Envía mensaje de descubrimiento a toda la red (broadcast)"""
    
    try:
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,     
                            b'\xff'*20,
                            0,             
                            0,     
                            b'\x00'*8,                
                            b'\x00'*50)             
        
        udp_socket.sendto(header, (BROADCAST_ADDR, PUERTO))
        print(f"[Descubrimiento] Echo enviado.")
        
    except Exception as e:
        print(f"[Error] Al enviar echo: {e}")


def manejar_echo(data, addr):
    """Procesa mensajes de descubrimiento(echo) recibidos"""
    
    try:
        id_origen = data[:20]
        
        if id_origen == mi_id:
            return
        
        usuarios_conectados[id_origen] = (addr[0], time.time())
        print(f"[Descubrimiento] Usuario encontrado: {id_origen.hex()} desde {addr[0]}")
        
        #Preparar respuesta (Reply)
        respuesta = struct.pack('!B 20s 4s',
                                0,              
                                mi_id,          
                                b'\x00'*4)     
        
        #Enviar respuesta al remitente original
        udp_socket.sendto(respuesta, addr)
            
    except Exception as e:
        print(f"[Error] Al procesar Echo: {e}")
        
        
def enviar_echos_periodicos():
    """Envía mensajes de descubrimiento periódicamente"""
    
    while True:
        enviar_echo()
        time.sleep(10)

################################
#Operación 1: Message-Response.
################################
def enviar_mensajes_texto(id_destino, mensaje):
    """
    Envía un mensaje de texto a otro usuario
    Devuelve True si tuvo éxito, False si hubo error
    """
    
    if id_destino not in usuarios_conectados:
        print(f"[Error] Usuario {id_destino.hex()} no encontrado")
        return False
    
    try:
        ip_destino = usuarios_conectados[id_destino][0]
        
        mensaje_id = int(time.time() * 1000)
        
        #Fase 1: Enviar header del mensaje.
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,     
                            id_destino,
                            1,             
                            mensaje_id % 256,     
                            len(mensaje).to_bytes(8, 'big'),                
                            b'\x00'*50)  
        
        udp_socket.sendto(header, (ip_destino, PUERTO))
        
        print("[Mensaje] Header enviado, esperando confirmación...")
        
        #Fase 2: Enviar cuerpo del mensaje.
        cuerpo = struct.pack('!Q', mensaje_id) + mensaje.encode('utf-8')
        udp_socket.sendto(cuerpo, (ip_destino, PUERTO))
        
        print(f"[Mensaje] Mensaje enviado a {id_destino.hex()}")
        return True
    
    except Exception as e:
        print(f"[Error] Al enviar mensaje: {e}")
        return False
    
def manejar_mensaje_recibido(data, addr):
    """Procesa un mensaje de texto recibido"""
    
    try:
        id_origen = data[:20]
        id_destino = data[20:40]
        operation_code = data[40]
        body_id = data[41]
        body_length = int.from_bytes(data[42:50], 'big')
        
        if id_destino != mi_id and id_destino != b'\xff'*20:
            return
        
        usuarios_conectados[id_origen] = (addr[0], time.time())
        
        if operation_code == 1: 
            #Enviar confirmación de header recibido
            respuesta = struct.pack('!B 20s 4s',
                                    0,              
                                    mi_id,          
                                    b'\x00'*4)      
            
            udp_socket.sendto(respuesta, addr)
            
            mensaje = "(Contenido del mensaje no implementado)"
            mensajes_recibidos.put((id_origen, mensaje))
            print(f"[Mensaje] Mensaje recibido de {id_origen.hex()}")
            
    except Exception as e:
        print(f"[Error] Al procesar mensaje: {e}")

########################################################
#Operación 2: Send File-Ack (Transferencia de archivos).
#########################################################
def enviar_archivo(id_destino, ruta_archivo):
    """Envía un archivo a otro usuario"""
    
    if id_destino not in usuarios_conectados:
        print(f"[Error] Usuario {id_destino.hex()} no encontrado")
        return False
    
    if not os.path.exists(ruta_archivo):
        print(f"[Error] Archivo no encontrado: {ruta_archivo}")
        return False
    
    try:
        ip_destino = usuarios_conectados[id_destino][0]
        
        file_id = int(time.time() * 1000)
        file_size = os.path.getsize(ruta_archivo)
        
        #Fase 1: Enviar header por UDP.
        header = struct.pack('!20s 20s B B 8s 50s',
                            mi_id,                 
                            id_destino,            
                            2,                     
                            file_id % 256,         
                            file_size.to_bytes(8, 'big'),  
                            b'\x00'*50)           
        
        udp_socket.sendto(header, (ip_destino, PUERTO))
        print("[Archivo] Header enviado, esperando confirmación...")
        
        #Fase 2: Enviar archivo por TCP.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_send_socket:
            tcp_send_socket.connect((ip_destino, PUERTO))
            
            # Enviar ID de archivo y contenido
            with open(ruta_archivo, 'rb') as f:
                tcp_send_socket.sendall(file_id.to_bytes(8, 'big'))
                
                while True:
                    data = f.read(4096)
                    if not data:
                        break
                    tcp_send_socket.sendall(data)
            
            print("[Archivo] Archivo enviado, esperando confirmación...")
            
        print(f"[Archivo] Archivo {ruta_archivo} enviado a {id_destino.hex()}")
        return True
    
    except Exception as e:
        print(f"[Error] Al enviar archivo: {e}")
        return False



if __name__ == "__main__":
    try:
        udp_socket, tcp_socket = iniciar_sockets()
        mi_id = generar_id_usuario()
        usuarios_conectados = {}  
        
        print(f"\nTu ID de usuario es: {mi_id.hex()}")
        print("Iniciando descubrimiento...\n")
        
        enviar_echo(udp_socket, mi_id)

        while True:
            try:
                data, addr = udp_socket.recvfrom(1024)
                
                if len(data) > 40:
                    op_code = data[40]
                    
                    if op_code == 0:
                        manejar_echo(udp_socket, data, addr, mi_id, usuarios_conectados)
            
            except KeyboardInterrupt:
                raise
            except:
                print("[Error] Paquete malformado")
                
            if time.time() % 5 < 0.1: 
                enviar_echo(udp_socket, mi_id)

    except KeyboardInterrupt:
        print("\nCerrando aplicación...")
        udp_socket.close()
        tcp_socket.close()