KDIR ?= /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

obj-m += vkgs_usb.o

ccflags-y += -Wall

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

install:
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

.PHONY: all install clean
