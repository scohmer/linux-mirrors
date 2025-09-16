Example disk creation script:

mkdir -p /media/usb/mirror/apt/{debian,ubuntu}
mkdir -p /media/usb/mirror/apt/debian/{archive.debian.org,deb.debian.org,security.debian.org}
mkdir -p /media/usb/mirror/apt/debian/archive.debian.org/debian
mkdir -p /media/usb/mirror/apt/debian/deb.debian.org/debian
mkdir -p /media/usb/mirror/apt/debian/security.debian.org/debian-security
mkdir -p /media/usb/mirror/apt/ubuntu/{archive.ubuntu.com,old-releases.ubuntu.com,security.ubuntu.com}
mkdir -p /media/usb/mirror/apt/ubuntu/archive.ubuntu.com/ubuntu
mkdir -p /media/usb/mirror/apt/ubuntu/old-releases.ubuntu.com/ubuntu
mkdir -p /media/usb/mirror/apt/ubuntu/security.ubuntu.com/ubuntu
mkdir -p /media/usb/mirror/yum/{rocky,rhel,epel}
mkdir -p /media/usb/mirror/yum/rocky/dl.rockylinux.org/pub/rocky
mkdir -p /media/usb/mirror/yum/rhel/cdn.redhat.com/pub/rhel
mkdir -p /media/usb/mirror/yum/epel/dl.fedoraproject.org/pub/epel

chmod 777 -R ~/mirrors/
wait
rsync -avph ~/mirrors/apt/debian/mirror/archive.debian.org/debian/* /media/usb/mirror/apt/debian/archive.debian.org/debian/ &
rsync -avph ~/mirrors/apt/debian/mirror/deb.debian.org/debian/* /media/usb/mirror/apt/debian/deb.debian.org/debian/ &
rsync -avph ~/mirrors/apt/debian/mirror/security.debian.org/debian-security/* /media/usb/mirror/apt/debian/security.debian.org/debian-security/ &
rsync -avph ~/mirrors/apt/ubuntu/mirror/archive.ubuntu.com/ubuntu/* /media/usb/mirror/apt/ubuntu/archive.ubuntu.com/ubuntu &
rsync -avph ~/mirrors/apt/ubuntu/mirror/old-releases.ubuntu.com/ubuntu/* /media/usb/mirror/apt/ubuntu/old-releases.ubuntu.com/ubuntu &
rsync -avph ~/mirrors/apt/ubuntu/mirror/security.ubuntu.com/ubuntu/* /media/usb/mirror/apt/ubuntu/security.ubuntu.com/ubuntu &
wait
find /media/usb/ -type d -exec chmod 755 {} \; &
find /media/usb/ -type f -exec chmod 644 {} \; &
wait
semanage fcontext -a -t httpd_sys_content_t -s system_u "/media/usb(/.*)?"
restorecon -R -v /media/usb/
