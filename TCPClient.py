from socket import *

#server name and port
serverName = '10.255.152.31'
serverPort = 12000

#create TCP socket for server, remote port 12000
clientSocket = socket(AF_INET, SOCK_STREAM)

clientSocket.connect((serverName,serverPort))

#get user input
sentence = input('Input lowercase sentence:')

#attach server name, port to sentence; send into socket
clientSocket.send(sentence.encode())

#read reply characters from socket into string no need for server name and port
modifiedsentence = clientSocket.recv(1024)

#print out received string and close socket
print('From Server:',modifiedsentence.decode())
clientSocket.close()